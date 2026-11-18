"""Connect the SoftEther VPN Client connection (e.g. AU-PIY -> 103.100.38.134) with vpncmd."""
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import is_set

VPNCMD_CANDIDATES = [
    r"C:\Program Files\SoftEther VPN Client\vpncmd_x64.exe",
    r"C:\Program Files\SoftEther VPN Client\vpncmd.exe",
    r"C:\Program Files (x86)\SoftEther VPN Client\vpncmd.exe",
]
ESTABLISHED = "session established"
NO_SUCH_ACCOUNT = "does not exist"


class VpnError(Exception):
    pass


def find_vpncmd(configured=""):
    for cand in [configured, shutil.which("vpncmd") or "", *VPNCMD_CANDIDATES]:
        if cand and Path(cand).exists():
            return cand
    raise VpnError("vpncmd (SoftEther VPN Client) not found. Set softether.vpncmd_path in config.json.")


def run_vpncmd(exe, *args, runner=subprocess.run):
    """Run one vpncmd client command and return its text output."""
    try:
        proc = runner([exe, "localhost", "/CLIENT", "/CMD", *args], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise VpnError(f"vpncmd {args[0]} did not answer within 60 s.") from None
    out = (proc.stdout or b"") + (proc.stderr or b"")
    return out.decode("utf-8", errors="replace") if isinstance(out, bytes) else str(out)


def parse_account_list(text):
    """[(name, status, host)] from `AccountList` output."""
    accounts, current = [], {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        key, _, value = line.partition("|")
        key, value = key.strip().lower(), value.strip()
        if key == "vpn connection setting name":
            if current:
                accounts.append(current)
            current = {"name": value, "status": "", "host": ""}
        elif current and key == "status":
            current["status"] = value
        elif current and key == "vpn server hostname":
            current["host"] = value
    if current:
        accounts.append(current)
    return [(a["name"], a["status"], a["host"]) for a in accounts]


def account_for_ip(accounts, ip):
    """The first saved connection whose server is ``ip`` (skipping "Copy of ..." duplicates when possible)."""
    matches = [a for a in accounts if a[2].split(":")[0].strip() == ip]
    originals = [a for a in matches if not a[0].lower().startswith("copy of")]
    chosen = (originals or matches)
    return chosen[0][0] if chosen else ""


def _last_message(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    useful = [ln for ln in lines if re.search(r"error|connect|exist|session", ln, re.I)]
    return (useful or lines or ["(no output)"])[-1][:200]


def connect(cfg, log, *, runner=subprocess.run, sleep=time.sleep, clock=time.monotonic):
    """Connect the SoftEther connection and wait for "Session Established". Returns the connection name."""
    vcfg = cfg.get("softether") or {}
    exe = find_vpncmd(vcfg.get("vpncmd_path", ""))
    accounts = parse_account_list(run_vpncmd(exe, "AccountList", runner=runner))
    name = str(vcfg.get("account_name") or "").strip() if is_set(vcfg.get("account_name")) else ""
    if not name:
        name = account_for_ip(accounts, cfg["expected_ip"])
        if not name:
            raise VpnError(f"No SoftEther connection points at {cfg['expected_ip']}. "
                           "Set softether.account_name in config.json.")
    known = {a[0].lower(): a for a in accounts}
    if accounts and name.lower() not in known:
        raise VpnError(f"SoftEther has no connection named '{name}'. Saved connections: "
                       f"{', '.join(a[0] for a in accounts)}.")
    others = [a[0] for a in accounts if a[0].lower() != name.lower() and "connected" in a[1].lower()
              and "offline" not in a[1].lower()]
    if others:
        log(f"Warning: other SoftEther connections are also on: {', '.join(others)}. "
            "The IP check will tell if traffic goes through the wrong one.")

    status = run_vpncmd(exe, "AccountStatusGet", name, runner=runner)
    if ESTABLISHED in status.lower():
        log(f"SoftEther: '{name}' is already connected.")
        return name

    log(f"SoftEther: connecting '{name}'...")
    out = run_vpncmd(exe, "AccountConnect", name, runner=runner)
    if NO_SUCH_ACCOUNT in out.lower():
        raise VpnError(f"SoftEther has no connection named '{name}'.")
    deadline = clock() + float(vcfg.get("connect_timeout_seconds", 60))
    last = out
    while clock() < deadline:
        sleep(2)
        last = run_vpncmd(exe, "AccountStatusGet", name, runner=runner)
        if ESTABLISHED in last.lower():
            log(f"SoftEther: '{name}' connected (session established).")
            return name
    raise VpnError(f"SoftEther connection '{name}' did not connect within "
                   f"{vcfg.get('connect_timeout_seconds', 60)} s. vpncmd said: {_last_message(last)}")
