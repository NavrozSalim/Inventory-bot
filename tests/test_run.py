"""run_inventory.main with the VPN, browser and portal replaced by fakes."""
import json

import pytest

import run_inventory
from core.files import INVENTORY
from core.ipcheck import IpCheckError
from core.report import COMPLETED, DASH_FAILED, DRY_RUN, FAILED, NOT_FOUND, SUBMITTED

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
    fails = {}          # (vendor, kind) -> how many tries fail before one works
    dash_states = []    # one list of states per verify call, for the uploads in order; default all COMPLETED

    def __init__(self, page, cfg, log, shots_dir, on_change=None):
        self.uploads = []
        self.verified = False
        self.verify_calls = 0
        FakePortal.instances.append(self)

    def sign_in(self):
        pass

    def snapshot_ids(self):
        return {"old"}

    def upload(self, item, *, dry_run=False):
        from datetime import datetime

        from core.report import UploadResult
        self.uploads.append((item.vendor, item.kind, dry_run))
        key = (item.vendor, item.kind)
        if FakePortal.fails.get(key):
            FakePortal.fails[key] -= 1
            return UploadResult(item=item, status=FAILED, message="TimeoutError: page did not load")
        return UploadResult(item=item, status=DRY_RUN if dry_run else SUBMITTED, uploaded_at=datetime.now(),
                            message="ok")

    def verify(self, results, known_ids):
        self.verified = True
        states = FakePortal.dash_states[self.verify_calls] if self.verify_calls < len(FakePortal.dash_states) else []
        self.verify_calls += 1
        for i, r in enumerate(x for x in results if x.status == SUBMITTED):
            r.dash_state = states[i] if i < len(states) else COMPLETED
            r.request_id = "" if r.dash_state == NOT_FOUND else f"req{i}"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    FakePortal.instances.clear()
    FakePortal.fails = {}
    FakePortal.dash_states = []
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


def test_failed_upload_is_tried_again(setup):
    _, cfg, z, _ = setup
    FakePortal.fails = {("Vevor", INVENTORY): 2}
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn"]) == 0
    portal = FakePortal.instances[0]
    assert [k for _, k, _ in portal.uploads] == [INVENTORY, INVENTORY, INVENTORY, "price"]
    assert portal.verify_calls == 1


def test_gives_up_after_three_tries(setup):
    tmp, cfg, z, _ = setup
    out = tmp / "results.json"
    FakePortal.fails = {("Vevor", "price"): 5}
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn",
                               "--results-json", str(out)]) == 1
    assert [k for _, k, _ in FakePortal.instances[0].uploads] == [INVENTORY, "price", "price", "price"]
    data = json.loads(out.read_text(encoding="utf-8"))["results"]
    assert [(r["kind"], r["status"], r["attempts"]) for r in data] == [(INVENTORY, SUBMITTED, 1),
                                                                      ("price", FAILED, 3)]


def test_failed_on_dashboard_is_uploaded_again(setup):
    _, cfg, z, _ = setup
    FakePortal.dash_states = [[COMPLETED, DASH_FAILED]]   # first check: the price import failed
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn"]) == 0
    portal = FakePortal.instances[0]
    assert [k for _, k, _ in portal.uploads] == [INVENTORY, "price", "price"]
    assert portal.verify_calls == 2


def test_not_found_only_retried_when_dashboard_shows_other_uploads(setup):
    _, cfg, z, _ = setup
    FakePortal.dash_states = [[NOT_FOUND, NOT_FOUND]]   # nothing found at all: the dashboard read may be broken
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn"]) == 1
    assert len(FakePortal.instances[0].uploads) == 2


def test_dashboard_failure_stops_after_three_tries(setup):
    _, cfg, z, _ = setup
    # each retry round is checked on its own: first both files, then the price re-uploads one at a time
    FakePortal.dash_states = [[COMPLETED, DASH_FAILED], [DASH_FAILED], [DASH_FAILED]]
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn"]) == 1
    portal = FakePortal.instances[0]
    assert [k for _, k, _ in portal.uploads] == [INVENTORY, "price", "price", "price"]
    assert portal.verify_calls == 3


def test_retry_round_checks_only_its_own_upload(setup):
    _, cfg, z, _ = setup
    seen = []
    FakePortal.dash_states = [[NOT_FOUND, COMPLETED]]   # the Stock/Status import never showed up
    orig = FakePortal.verify

    def verify(self, results, known_ids):
        seen.append([(r.item.kind, r.attempt) for r in results])
        return orig(self, results, known_ids)

    FakePortal.verify = verify
    try:
        assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn"]) == 0
    finally:
        FakePortal.verify = orig
    assert seen == [[(INVENTORY, 1), ("price", 1)], [(INVENTORY, 2)]]


def test_unexpected_error_stops_cleanly(setup, monkeypatch):
    tmp, cfg, z, _ = setup
    out = tmp / "results.json"

    def boom(self, results, known_ids):
        raise RuntimeError("Page.goto: net::ERR_CONNECTION_CLOSED")

    monkeypatch.setattr(FakePortal, "verify", boom)
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn",
                               "--results-json", str(out)]) == 1   # dashboard error: results kept
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["results"]) == 2

    def boom_sign_in(self):
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(FakePortal, "sign_in", boom_sign_in)
    assert run_inventory.main(["--config", str(cfg), "--zip", str(z), "--skip-vpn",
                               "--results-json", str(out)]) == 2
    assert json.loads(out.read_text(encoding="utf-8"))["message"].startswith("STOPPED: RuntimeError")
