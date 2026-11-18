"""run_inventory.main with the VPN, browser and portal replaced by fakes."""
import json

import pytest

import run_inventory
from core.files import INVENTORY
from core.ipcheck import IpCheckError
from core.report import COMPLETED, DRY_RUN, SUBMITTED

from .helpers import vendor_zip


class FakePage:
    def wait_for_timeout(self, ms):
        pass


class FakeSession:
    def __init__(self, cfg):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def new_page(self):
        return FakePage()


class FakePortal:
    instances = []

    def __init__(self, page, cfg, log, shots_dir, on_change=None):
        self.uploads = []
        self.verified = False
        FakePortal.instances.append(self)

    def sign_in(self):
        pass

    def snapshot_ids(self):
        return {"old"}

    def upload(self, item, *, dry_run=False):
        from datetime import datetime

        from core.report import UploadResult
        self.uploads.append((item.vendor, item.kind, dry_run))
        return UploadResult(item=item, status=DRY_RUN if dry_run else SUBMITTED, uploaded_at=datetime.now(),
                            message="ok")

    def verify(self, results, known_ids):
        self.verified = True
        for r in results:
            r.dash_state = COMPLETED


@pytest.fixture
def setup(tmp_path, monkeypatch):
    FakePortal.instances.clear()
    monkeypatch.setattr(run_inventory, "ROOT", tmp_path)
    monkeypatch.setattr(run_inventory, "BrowserSession", FakeSession)
    monkeypatch.setattr(run_inventory, "Portal", FakePortal)
    monkeypatch.setattr(run_inventory, "check_browser_ip", lambda page, cfg, log: cfg["expected_ip"])
    calls = []
    monkeypatch.setattr(run_inventory, "connect", lambda cfg, log: calls.append("vpn"))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({}), encoding="utf-8")
    z = vendor_zip(tmp_path / "input" / "z.zip", {"Vevor": (["V1"], ["V1"]), "Unmapped": (["U"], ["U"])})
    return tmp_path, cfg, z, calls


def test_full_run_uploads_each_vendor_file_then_verifies(setup):
    tmp, cfg, z, calls = setup
    code = run_inventory.main(["--config", str(cfg)])
    assert code == 0
    assert calls == ["vpn"]
    portal = FakePortal.instances[0]
    assert portal.uploads == [("Vevor", INVENTORY, False), ("Vevor", "price", False)]
    assert portal.verified
    log = next((tmp / "logs").glob("inventory-bot-*.log")).read_text(encoding="utf-8")
    assert "Inventory updated: Vevor" in log and "Price updated: Vevor" in log
    assert list((tmp / "logs").glob("inventory-run-*.csv"))
    assert not list((tmp / "work").glob("*/*"))   # extracted files removed


def test_kinds_and_results_json(setup):
    tmp, cfg, z, _ = setup
    out = tmp / "results.json"
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--kinds", "price",
                               "--results-json", str(out)]) == 0
    assert FakePortal.instances[0].uploads == [("Vevor", "price", False)]
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["message"].startswith("Finished: 1 vendor(s), 1 file(s): 1 completed")
    assert data["results"][0]["vendor"] == "Vevor" and data["results"][0]["dash_state"] == "completed"


def test_bad_kinds_value(setup):
    _, cfg, _, _ = setup
    with pytest.raises(SystemExit):
        run_inventory.main(["--config", str(cfg), "--kinds", "stock"])


def test_stop_message_in_results_json(setup, monkeypatch):
    tmp, cfg, _, _ = setup
    out = tmp / "results.json"

    def wrong(page, cfg, log):
        raise IpCheckError("Wrong IP")

    monkeypatch.setattr(run_inventory, "check_browser_ip", wrong)
    assert run_inventory.main(["--config", str(cfg), "--results-json", str(out)]) == 2
    assert json.loads(out.read_text(encoding="utf-8"))["message"] == "STOPPED: Wrong IP"


def test_dry_run_does_not_verify(setup):
    _, cfg, z, _ = setup
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--dry-run", "--skip-vpn"]) == 0
    portal = FakePortal.instances[0]
    assert all(dry for _, _, dry in portal.uploads) and not portal.verified


def test_wrong_ip_stops_before_any_upload(setup, monkeypatch):
    _, cfg, _, _ = setup

    def wrong(page, cfg, log):
        raise IpCheckError("Wrong IP")

    monkeypatch.setattr(run_inventory, "check_browser_ip", wrong)
    assert run_inventory.main(["--config", str(cfg)]) == 2
    assert FakePortal.instances == []


def test_missing_config_stops(setup):
    tmp, _, _, calls = setup
    assert run_inventory.main(["--config", str(tmp / "nope.json")]) == 2
    assert calls == []
