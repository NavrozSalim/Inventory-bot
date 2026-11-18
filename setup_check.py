"""Check this laptop has what the bot needs (run by setup.bat; safe to run any time).

Checks: config.json, Chrome, SoftEther vpncmd and the VPN connection for expected_ip, Playwright.
Changes nothing except creating config.json from the example when it is missing.
"""
import shutil
import sys

from core.config import ROOT, ConfigError, load_config


def main():
    ok = True

    def line(good, text):
        nonlocal ok
        ok = ok and good
        print(f"  [{'OK' if good else '!!'}] {text}")

    print("Checking this laptop:")
    if not (ROOT / "config.json").exists():
        shutil.copy(ROOT / "config.example.json", ROOT / "config.json")
        print("  [OK] config.json created from config.example.json")
    try:
        cfg = load_config()
        line(True, "config.json is valid")
    except ConfigError as e:
        line(False, str(e))
        return 1

    try:
        import playwright  # noqa: F401
        line(True, "Playwright installed")
    except ImportError:
        line(False, "Playwright missing: run  python -m pip install -r requirements.txt")

    from core.browser import BrowserStartError, chrome_exe
    try:
        line(True, f"Chrome: {chrome_exe(cfg)}")
    except BrowserStartError:
        line(False, "Google Chrome not found. Install Chrome, or set chrome_path in config.json.")

    from core.vpn import VpnError, account_for_ip, find_vpncmd, parse_account_list, run_vpncmd
    try:
        exe = find_vpncmd(cfg["softether"].get("vpncmd_path", ""))
        line(True, f"SoftEther vpncmd: {exe}")
        accounts = parse_account_list(run_vpncmd(exe, "AccountList"))
        name = str(cfg["softether"].get("account_name") or "").strip()
        names = {a[0].lower(): a for a in accounts}
        by_ip = account_for_ip(accounts, cfg["expected_ip"])
        if name and name.lower() in names:
            line(True, f"SoftEther connection '{name}' ({names[name.lower()][2]})")
        elif by_ip:
            line(False, f"SoftEther has no connection named '{name}', but '{by_ip}' points at "
                        f"{cfg['expected_ip']}. Set softether.account_name to \"{by_ip}\" in config.json.")
        else:
            line(False, f"SoftEther has no connection to {cfg['expected_ip']}. Add it in SoftEther VPN Client "
                        "Manager, then set softether.account_name in config.json.")
    except VpnError as e:
        line(False, f"{e} Install SoftEther VPN Client.")

    print()
    if ok:
        print("Ready. Next:")
    else:
        print("Fix the [!!] lines above, then run setup.bat again. After that:")
    print("  1. Turn on the VPN, then sign in once:   python open_profile.py")
    print("  2. Put the 'Templates by Vendor' ZIP in the input folder")
    print("  3. Watch a dry run:                     python run_inventory.py --dry-run")
    print("  4. Real run:                            run_inventory.bat")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
