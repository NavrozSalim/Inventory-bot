"""Check, in the bot's Chrome, that the public IP is the VPN's (NordVPN IP lookup page)."""
import re
import time

IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


class IpCheckError(Exception):
    pass


def ips_in_text(text):
    found = []
    for m in IPV4.findall(text or ""):
        if all(0 <= int(part) <= 255 for part in m.split(".")) and m not in found:
            found.append(m)
    return found


def verify_ip_text(text, expected):
    """Return ``expected`` when the page shows it; otherwise raise with the IP the page shows."""
    ips = ips_in_text(text)
    if expected in ips:
        return expected
    if not ips:
        raise IpCheckError("The IP lookup page showed no IP address.")
    raise IpCheckError(f"Wrong IP: the IP lookup page shows {ips[0]}, expected {expected}. "
                       "The VPN is not carrying this browser's traffic; nothing was uploaded.")


def _check_once(page, url, expected, timeout_s, clock):
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as e:   # the VPN may still be settling
        raise IpCheckError(f"Could not open the IP lookup page: {type(e).__name__}: {str(e)[:150]}") from None
    deadline = clock() + timeout_s
    text = ""
    while clock() < deadline:
        try:
            text = page.inner_text("body", timeout=5000)
        except Exception:
            text = ""
        if ips_in_text(text):   # the page fills the IP in with JavaScript
            break
        page.wait_for_timeout(1500)
    return verify_ip_text(text, expected)


def check_browser_ip(page, cfg, log, *, clock=time.monotonic):
    """Open the IP lookup page and check the IP, up to ip_check_attempts times (3), reloading each time."""
    url = cfg["ip_check_url"]
    expected = cfg["expected_ip"]
    tries = max(1, int(cfg.get("ip_check_attempts", 3)))
    gap_ms = int(float(cfg.get("seconds_between_ip_checks", 10)) * 1000)
    timeout_s = float(cfg.get("ip_check_timeout_seconds", 30))
    for attempt in range(1, tries + 1):
        log(f"Checking the IP on {url} (try {attempt} of {tries}) ...")
        try:
            ip = _check_once(page, url, expected, timeout_s, clock)
        except IpCheckError as e:
            if attempt == tries:
                raise IpCheckError(f"{e} (after {tries} tries)") from None
            log(f"IP check try {attempt} of {tries} failed: {e} Checking again in {gap_ms // 1000} s.")
            page.wait_for_timeout(gap_ms)
            continue
        log(f"IP check passed: {ip}.")
        return ip
