import pytest

from core.vpn import VpnError, account_for_ip, connect, parse_account_list

ACCOUNT_LIST = """\
Item                        |Value
----------------------------+----------------------------------------------
VPN Connection Setting Name |AU-IP2
Status                      |Offline
VPN Server Hostname         |203.57.51.216:443 (Direct TCP/IP Connection)
----------------------------+----------------------------------------------
VPN Connection Setting Name |AU-PIY
Status                      |Offline
VPN Server Hostname         |103.100.38.134:443 (Direct TCP/IP Connection)
----------------------------+----------------------------------------------
VPN Connection Setting Name |Copy of AU-PIY
Status                      |Offline
VPN Server Hostname         |103.100.38.134:443 (Direct TCP/IP Connection)
The command completed successfully.
"""
ESTABLISHED = "Session Status |Connection Completed (Session Established)\n"
NOT_CONNECTED = "Error occurred. (Error code: 37)\nThe specified VPN Connection Setting is not connected.\n"


class Proc:
    def __init__(self, out):
        self.stdout = out.encode()
        self.stderr = b""


class FakeVpncmd:
    def __init__(self, statuses, connect_out="The command completed successfully.", account_list=ACCOUNT_LIST):
        self.statuses = list(statuses)
        self.connect_out = connect_out
        self.account_list = account_list
        self.calls = []

    def __call__(self, cmd, **kw):
        args = cmd[4:]
        self.calls.append(args)
        if args[0] == "AccountList":
            return Proc(self.account_list)
        if args[0] == "AccountConnect":
            return Proc(self.connect_out)
        if args[0] == "AccountStatusGet":
            return Proc(self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0])
        raise AssertionError(cmd)


@pytest.fixture
def cfg(tmp_path):
    exe = tmp_path / "vpncmd.exe"
    exe.write_text("")
    return {"softether": {"vpncmd_path": str(exe), "account_name": "AU-PIY", "connect_timeout_seconds": 10},
            "expected_ip": "103.100.38.134"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(cfg, fake):
    logs, clock = [], Clock()
    name = connect(cfg, logs.append, runner=fake, sleep=clock.sleep, clock=clock)
    return name, logs


def test_parse_account_list_and_pick_by_ip():
    accounts = parse_account_list(ACCOUNT_LIST)
    assert [a[0] for a in accounts] == ["AU-IP2", "AU-PIY", "Copy of AU-PIY"]
    assert account_for_ip(accounts, "103.100.38.134") == "AU-PIY"
    assert account_for_ip(accounts, "1.2.3.4") == ""


def test_already_connected_does_not_reconnect(cfg):
    fake = FakeVpncmd([ESTABLISHED])
    name, logs = run(cfg, fake)
    assert name == "AU-PIY"
    assert not any(c[0] == "AccountConnect" for c in fake.calls)
    assert "already connected" in logs[-1]


def test_connects_and_waits_for_session(cfg):
    fake = FakeVpncmd([NOT_CONNECTED, NOT_CONNECTED, "Connecting...", ESTABLISHED])
    name, logs = run(cfg, fake)
    assert ["AccountConnect", "AU-PIY"] in fake.calls
    assert "connected (session established)" in logs[-1]


def test_timeout_raises(cfg):
    fake = FakeVpncmd([NOT_CONNECTED])
    with pytest.raises(VpnError, match="did not connect within 10"):
        run(cfg, fake)


def test_unknown_connection_name(cfg):
    cfg["softether"]["account_name"] = "NOPE"
    with pytest.raises(VpnError, match="no connection named 'NOPE'"):
        run(cfg, FakeVpncmd([ESTABLISHED]))


def test_blank_name_uses_connection_for_expected_ip(cfg):
    cfg["softether"]["account_name"] = ""
    name, _ = run(cfg, FakeVpncmd([ESTABLISHED]))
    assert name == "AU-PIY"


def test_missing_vpncmd(cfg, monkeypatch):
    cfg["softether"]["vpncmd_path"] = r"Z:\nowhere\vpncmd.exe"
    monkeypatch.setattr("core.vpn.VPNCMD_CANDIDATES", [])
    monkeypatch.setattr("core.vpn.shutil.which", lambda _: None)
    with pytest.raises(VpnError, match="vpncmd"):
        run(cfg, FakeVpncmd([ESTABLISHED]))


def test_warns_when_another_connection_is_on(cfg):
    listing = ACCOUNT_LIST.replace("Status                      |Offline\nVPN Server Hostname         |203",
                                   "Status                      |Connected\nVPN Server Hostname         |203")
    _, logs = run(cfg, FakeVpncmd([ESTABLISHED], account_list=listing))
    assert any("AU-IP2" in line and "Warning" in line for line in logs)
