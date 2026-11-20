import pytest

from core.ipcheck import IpCheckError, check_browser_ip, ips_in_text, verify_ip_text


def test_ips_in_text_ignores_invalid_and_versions():
    text = "Your IP address 103.100.38.134 Location Sydney. Version 1.2.3.4.5 bad 300.1.1.1"
    assert ips_in_text(text) == ["103.100.38.134"]


def test_verify_ok():
    assert verify_ip_text("What is my IP? 103.100.38.134", "103.100.38.134") == "103.100.38.134"


def test_verify_wrong_ip():
    with pytest.raises(IpCheckError, match="shows 39.40.1.2, expected 103.100.38.134"):
        verify_ip_text("Your IP address 39.40.1.2", "103.100.38.134")


def test_verify_no_ip():
    with pytest.raises(IpCheckError, match="no IP"):
        verify_ip_text("Loading...", "103.100.38.134")


class FakePage:
    def __init__(self, texts, goto_errors=0):
        self.texts = list(texts)
        self.url = ""
        self.gotos = 0
        self.goto_errors = goto_errors
        self.waits = []

    def goto(self, url, **kw):
        self.gotos += 1
        if self.goto_errors:
            self.goto_errors -= 1
            raise TimeoutError("net::ERR_TIMED_OUT")
        self.url = url

    def inner_text(self, sel, **kw):
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def test_check_browser_ip_waits_for_the_page_to_fill_in():
    cfg = {"ip_check_url": "https://nordvpn.com/ip-lookup/", "expected_ip": "103.100.38.134",
           "ip_check_timeout_seconds": 30}
    page = FakePage(["Loading", "Loading", "Your IP address 103.100.38.134"])
    logs = []
    assert check_browser_ip(page, cfg, logs.append) == "103.100.38.134"
    assert page.url == "https://nordvpn.com/ip-lookup/"
    assert "passed" in logs[-1]
    assert page.gotos == 1


class Clock:
    """Each call moves 10 s on, so one try's 30 s wait ends after a few reads."""
    def __init__(self):
        self.t = 0

    def __call__(self):
        self.t += 10
        return self.t


CFG = {"ip_check_url": "https://nordvpn.com/ip-lookup/", "expected_ip": "103.100.38.134",
       "ip_check_timeout_seconds": 30, "seconds_between_ip_checks": 10}


def test_wrong_ip_is_checked_again_and_passes_on_a_later_try():
    page = FakePage(["Your IP address 39.40.1.2"] * 2 + ["Your IP address 103.100.38.134"])
    logs = []
    assert check_browser_ip(page, CFG, logs.append, clock=Clock()) == "103.100.38.134"
    assert page.gotos == 3
    assert any("try 1 of 3 failed: Wrong IP" in line for line in logs)
    assert 10000 in page.waits


def test_page_that_does_not_open_is_tried_again():
    page = FakePage(["Your IP address 103.100.38.134"], goto_errors=2)
    logs = []
    assert check_browser_ip(page, CFG, logs.append, clock=Clock()) == "103.100.38.134"
    assert page.gotos == 3
    assert "Could not open the IP lookup page" in logs[1]


def test_stops_after_three_tries():
    page = FakePage(["Your IP address 39.40.1.2"])
    logs = []
    with pytest.raises(IpCheckError, match=r"shows 39.40.1.2, expected 103.100.38.134.*\(after 3 tries\)"):
        check_browser_ip(page, CFG, logs.append, clock=Clock())
    assert page.gotos == 3
    assert sum("Checking the IP" in line for line in logs) == 3
