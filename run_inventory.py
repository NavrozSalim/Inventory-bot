"""MyDeal inventory bot: upload each vendor's Stock/Status and Price file to Woolworths MarketPlus.

  python run_inventory.py                       newest ZIP in input/
  python run_inventory.py --zip "path.zip"      a "Templates by Vendor" ZIP from the web app
  python run_inventory.py --dry-run             everything except clicking Import
  python run_inventory.py --only "Amazon AU" --only Vevor
"""
import argparse
import shutil
import sys
from datetime import datetime

from core.browser import BrowserSession, BrowserStartError
from core.config import ROOT, ConfigError, load_config
from core.files import FilesError, collect_files, newest_zip
from core.ipcheck import IpCheckError, check_browser_ip
from core.portal import Portal, PortalError
from core.report import (DRY_RUN, SUBMITTED, RunLog, all_ok, result_line, summary, write_csv)
from core.vpn import VpnError, connect

STOP_ERRORS = (ConfigError, FilesError, VpnError, IpCheckError, PortalError, BrowserStartError)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Upload MyDeal inventory and price files, one vendor at a time.")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--zip", help="'Templates by Vendor' ZIP from the web app (default: newest ZIP in input/)")
    src.add_argument("--folder", help="folder with one sub-folder of CSV files per vendor")
    p.add_argument("--config", help="settings file (default config.json)")
    p.add_argument("--dry-run", action="store_true", help="choose each file but do not click Import")
    p.add_argument("--include-unmapped", action="store_true", help="also upload the Unmapped SKUs files")
    p.add_argument("--only", action="append", default=[], help="vendor name to upload (repeat for more)")
    p.add_argument("--skip-vpn", action="store_true",
                   help="do not connect SoftEther (the IP check still runs and must pass)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = RunLog(ROOT / "logs", stamp)
    work_dir = ROOT / "work" / stamp
    results = []
    log(f"=== MyDeal inventory bot run {stamp}{' (DRY RUN)' if args.dry_run else ''} ===")
    try:
        cfg = load_config(args.config)
        source = args.zip or args.folder or newest_zip(ROOT / "input")
        log(f"Files: {source}")
        only = [v.strip() for o in args.only for v in o.split(",")]
        items, skipped = collect_files(
            source, work_dir, include_unmapped=args.include_unmapped, only=only,
            max_rows=int(cfg.get("max_rows_per_file") or 0), upload_order=cfg.get("upload_order", "vendor"))
        for name, reason in skipped:
            log(f"Skipped {name}: {reason}")
        if not items:
            log("Nothing to upload.")
            return 2
        store = next((it.store for it in items if it.store), "")
        log(f"{len(items)} file(s) to upload{f' for {store}' if store else ''}:")
        for it in items:
            log(f"  {it.label}: {it.path.name} ({it.rows} rows)")

        if args.skip_vpn:
            log("SoftEther: skipped (--skip-vpn).")
        else:
            connect(cfg, log)

        with BrowserSession(cfg) as session:
            page = session.new_page()
            check_browser_ip(page, cfg, log)
            portal = Portal(page, cfg, log, ROOT / "logs" / "screenshots" / stamp)
            portal.sign_in()
            known_ids = set()
            if not args.dry_run:
                try:
                    known_ids = portal.snapshot_ids()
                except PortalError as e:
                    log(f"Warning: {e} Rows will be matched by type and order only.")
            gap = int(float(cfg.get("seconds_between_uploads", 5)) * 1000)
            for n, item in enumerate(items, start=1):
                log(f"[{n}/{len(items)}] Uploading {item.label} ({item.rows} rows)...")
                r = portal.upload(item, dry_run=args.dry_run)
                results.append(r)
                if r.status == DRY_RUN:
                    log(f"  Dry run: file chosen, Import not clicked.")
                elif r.status == SUBMITTED:
                    log(f"  Imported at {r.uploaded_at:%d/%m/%Y %H:%M:%S}. Portal said: {r.message}")
                else:
                    log(f"  {r.status.upper()}: {r.message}")
                if n < len(items):
                    page.wait_for_timeout(gap)
            if not args.dry_run:
                try:
                    portal.verify(results, known_ids)
                except PortalError as e:
                    log(f"Could not check the Import Request Dashboard: {e}")

        log("--- Results ---")
        for r in results:
            log(result_line(r))
        log(summary(results))
        log(f"Summary file: {write_csv(ROOT / 'logs' / f'inventory-run-{stamp}.csv', results)}")
        return 0 if all_ok(results) else 1
    except STOP_ERRORS as e:
        log(f"STOPPED: {e}")
        if results:
            for r in results:
                log(result_line(r))
            write_csv(ROOT / "logs" / f"inventory-run-{stamp}.csv", results)
        return 2
    except KeyboardInterrupt:
        log("Stopped by the user (Ctrl+C).")
        return 130
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
