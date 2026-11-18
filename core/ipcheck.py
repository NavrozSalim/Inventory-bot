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


def check_browser_ip(page, cfg, log, *, clock=time.monotonic):
    url = cfg["ip_check_url"]
    expected = cfg["expected_ip"]
    log(f"Checking the IP on {url} ...")
    page.goto(url, wait_until="domcontentloaded")
    deadline = clock() + float(cfg.get("ip_check_timeout_seconds", 30))
    text = ""
    while clock() < deadline:
        try:
            text = page.inner_text("body", timeout=5000)
        except Exception:
            text = ""
        if ips_in_text(text):   # the page fills the IP in with JavaScript
            break
        page.wait_for_timeout(1500)
    ip = verify_ip_text(text, expected)
    log(f"IP check passed: {ip}.")
    return ip
