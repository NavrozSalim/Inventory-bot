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
    def __init__(self, texts):
        self.texts = list(texts)
        self.url = ""

    def goto(self, url, **kw):
        self.url = url

    def inner_text(self, sel, **kw):
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]

    def wait_for_timeout(self, ms):
        pass


def test_check_browser_ip_waits_for_the_page_to_fill_in():
    cfg = {"ip_check_url": "https://nordvpn.com/ip-lookup/", "expected_ip": "103.100.38.134",
           "ip_check_timeout_seconds": 30}
    page = FakePage(["Loading", "Loading", "Your IP address 103.100.38.134"])
    logs = []
    assert check_browser_ip(page, cfg, logs.append) == "103.100.38.134"
    assert page.url == "https://nordvpn.com/ip-lookup/"
    assert "passed" in logs[-1]
