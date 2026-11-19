"""MyDeal inventory bot: upload each vendor's Stock/Status and Price file to Woolworths MarketPlus.

  python run_inventory.py                       newest ZIP in input/
  python run_inventory.py --zip "path.zip"      a "Templates by Vendor" ZIP from the web app
  python run_inventory.py --dry-run             everything except clicking Import
  python run_inventory.py --only "Amazon AU" --only Vevor
  python run_inventory.py --kinds inventory     only the Stock/Status files

The laptop agent (agent.py) runs this with --zip and --results-json for jobs started from the web app.
"""
import argparse
import shutil
import sys
from datetime import datetime

from core.attention import clear as clear_attention
from core.browser import BrowserSession, BrowserStartError
from core.config import ROOT, ConfigError, load_config
from core.files import INVENTORY, PRICE, FilesError, collect_files, newest_zip
from core.ipcheck import IpCheckError, check_browser_ip
from core.portal import Portal, PortalError
from core.report import (DASH_FAILED, DRY_RUN, NOT_FOUND, SUBMITTED, RunLog, all_ok, result_line, summary,
                         write_csv, write_results_json)
from core.vpn import VpnError, connect

STOP_ERRORS = (ConfigError, FilesError, VpnError, IpCheckError, PortalError, BrowserStartError)


def needs_another_try(r, uploads, max_tries):
    """Upload again after the dashboard check: the import FAILED there, or it never showed up (only when the
    dashboard did show other uploads of this run, so a dashboard that cannot be read does not re-upload all)."""
    if r.status != SUBMITTED or r.attempt >= max_tries:
        return False
    if r.dash_state == DASH_FAILED:
        return True
    return r.dash_state == NOT_FOUND and any(u.request_id for u in uploads)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Upload MyDeal inventory and price files, one vendor at a time.")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--zip", help="'Templates by Vendor' ZIP from the web app (default: newest ZIP in input/)")
    src.add_argument("--folder", help="folder with one sub-folder of CSV files per vendor")
    p.add_argument("--config", help="settings file (default config.json)")
    p.add_argument("--dry-run", action="store_true", help="choose each file but do not click Import")
    p.add_argument("--include-unmapped", action="store_true", help="also upload the Unmapped SKUs files")
    p.add_argument("--only", action="append", default=[], help="vendor name to upload (repeat for more)")
    p.add_argument("--kinds", action="append", default=[],
                   help="inventory and/or price (default both; repeat or comma-separate)")
    p.add_argument("--skip-vpn", action="store_true",
                   help="do not connect SoftEther (the IP check still runs and must pass)")
    p.add_argument("--results-json", help="keep this JSON file updated with each file's result (used by agent.py)")
    args = p.parse_args(argv)
    kinds = [k.strip().lower() for raw in args.kinds for k in raw.split(",") if k.strip()]
    bad = [k for k in kinds if k not in (INVENTORY, PRICE)]
    if bad:
        p.error(f"--kinds must be inventory or price, not {', '.join(bad)}")
    args.kinds = kinds
    return args


def main(argv=None):
    args = parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = RunLog(ROOT / "logs", stamp)
    work_dir = ROOT / "work" / stamp
    results = []

    def save(message=""):
        try:
            write_results_json(args.results_json, results, message)
        except OSError as e:
            log(f"Warning: could not write {args.results_json}: {e}")

    log(f"=== MyDeal inventory bot run {stamp}{' (DRY RUN)' if args.dry_run else ''} ===")
    try:
        cfg = load_config(args.config)
        source = args.zip or args.folder or newest_zip(ROOT / "input")
        log(f"Files: {source}")
        only = [v.strip() for o in args.only for v in o.split(",")]
        items, skipped = collect_files(
            source, work_dir, include_unmapped=args.include_unmapped, only=only,
            max_rows=int(cfg.get("max_rows_per_file") or 0), upload_order=cfg.get("upload_order", "vendor"),
            kinds=args.kinds or None)
        for name, reason in skipped:
            log(f"Skipped {name}: {reason}")
        if not items:
            log("Nothing to upload.")
            save("Nothing to upload.")
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
            portal = Portal(page, cfg, log, ROOT / "logs" / "screenshots" / stamp, on_change=save)
            portal.sign_in()
            known_ids = set()
            if not args.dry_run:
                try:
                    known_ids = portal.snapshot_ids()
                except PortalError as e:
                    log(f"Warning: {e} Rows will be matched by type and order only.")
            gap = int(float(cfg.get("seconds_between_uploads", 5)) * 1000)
            max_tries = max(1, int(cfg.get("upload_attempts", 3)))
            retry_gap = int(float(cfg.get("seconds_between_attempts", 15)) * 1000)
            uploads = []   # every try, in order, for matching the dashboard rows

            def upload_file(idx, first_try):
                item = items[idx]
                for attempt in range(first_try, max_tries + 1):
                    tries = f", try {attempt} of {max_tries}" if attempt > 1 else ""
                    log(f"[{idx + 1}/{len(items)}] Uploading {item.label} ({item.rows} rows{tries})...")
                    r = portal.upload(item, dry_run=args.dry_run)
                    r.attempt = attempt
                    uploads.append(r)
                    if idx < len(results):
                        results[idx] = r
                    else:
                        results.append(r)
                    save()
                    if r.status == DRY_RUN:
                        log("  Dry run: file chosen, Import not clicked.")
                        return
                    if r.status == SUBMITTED:
                        log(f"  Imported at {r.uploaded_at:%d/%m/%Y %H:%M:%S}. Portal said: {r.message}")
                        return
                    if attempt < max_tries:
                        log(f"  {r.status.upper()} (try {attempt} of {max_tries}): {r.message}. "
                            f"Uploading it again in {retry_gap // 1000} s.")
                        page.wait_for_timeout(retry_gap)
                    else:
                        log(f"  {r.status.upper()} after {max_tries} tries: {r.message}")

            for n in range(len(items)):
                upload_file(n, 1)
                if n < len(items) - 1:
                    page.wait_for_timeout(gap)
            if not args.dry_run:
                try:
                    while True:
                        portal.verify(uploads, known_ids)
                        again = [i for i, r in enumerate(results) if needs_another_try(r, uploads, max_tries)]
                        if not again:
                            break
                        for i in again:
                            r = results[i]
                            why = ("FAILED on the Import Request Dashboard" if r.dash_state == DASH_FAILED
                                   else "not found on the Import Request Dashboard")
                            log(f"{items[i].label} {why}; uploading it again (try {r.attempt + 1} of {max_tries}).")
                            upload_file(i, r.attempt + 1)
                            page.wait_for_timeout(gap)
                except PortalError as e:
                    log(f"Could not check the Import Request Dashboard: {e}")

        log("--- Results ---")
        for r in results:
            log(result_line(r))
        done = summary(results)
        log(done)
        log(f"Summary file: {write_csv(ROOT / 'logs' / f'inventory-run-{stamp}.csv', results)}")
        save(done)
        return 0 if all_ok(results) else 1
    except STOP_ERRORS as e:
        log(f"STOPPED: {e}")
        if results:
            for r in results:
                log(result_line(r))
            write_csv(ROOT / "logs" / f"inventory-run-{stamp}.csv", results)
        save(f"STOPPED: {e}")
        return 2
    except KeyboardInterrupt:
        log("Stopped by the user (Ctrl+C).")
        save("Stopped on the laptop (Ctrl+C).")
        return 130
    finally:
        clear_attention()
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
