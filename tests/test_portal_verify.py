"""Portal.verify with the browser replaced: lost connections, empty reads and an upload that never shows up."""
from datetime import datetime
from pathlib import Path

import pytest

from core.dashboard import DashRow
from core.files import INVENTORY, PRICE, UploadItem
from core.portal import Portal, PortalError
from core.report import COMPLETED, NOT_FOUND, SUBMITTED, UploadResult


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Page:
    def __init__(self, clock):
        self.clock = clock

    def wait_for_timeout(self, ms):
        self.clock.t += ms / 1000


class FakeDashPortal(Portal):
    """Each check returns the next entry of reads: a list of rows, or an exception to raise."""

    def __init__(self, reads, cfg=None):
        self.clk = Clock()
        cfg = {"portal": {}, "wait_after_upload_minutes": 0, "dashboard_extra_wait_minutes": 15,
               "missing_row_wait_minutes": 5, "dashboard_refresh_seconds": 30, **(cfg or {})}
        self.logs = []
        super().__init__(Page(self.clk), cfg, self.logs.append, Path("."), clock=self.clk)
        self.reads = list(reads)

    def open_dashboard(self):
        nxt = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        if isinstance(nxt, Exception):
            raise nxt
        self._next = nxt

    def read_rows(self, known_ids=None, max_pages=5):
        return self._next

    def screenshot(self, name):
        return ""


def up(kind):
    item = UploadItem("VevorAU", kind, Path(f"{kind}.csv"), 10)
    return UploadResult(item=item, status=SUBMITTED, uploaded_at=datetime(2026, 11, 20, 4, 1))


OLD = DashRow("old", "Import Price Request", "19/11/2026 10:00", "COMPLETED")
PRICE_ROW = DashRow("p1", "Import Price Request", "20/11/2026 04:02", "COMPLETED")
STOCK_ROW = DashRow("s1", "Import Stock/Status Request", "20/11/2026 04:02", "COMPLETED")


def test_connection_closed_and_empty_read_are_checked_again():
    portal = FakeDashPortal([RuntimeError("Page.goto: net::ERR_CONNECTION_CLOSED"), [],
                             [STOCK_ROW, PRICE_ROW, OLD]])
    results = [up(INVENTORY), up(PRICE)]
    portal.verify(results, {"old"})
    assert [r.dash_state for r in results] == [COMPLETED, COMPLETED]
    assert any("Dashboard did not load: RuntimeError" in line for line in portal.logs)
    assert any("no rows (still loading)" in line for line in portal.logs)


def test_upload_that_never_shows_up_is_given_up_after_missing_row_wait():
    portal = FakeDashPortal([[PRICE_ROW, OLD]])
    results = [up(INVENTORY), up(PRICE)]
    portal.verify(results, {"old"})
    assert [r.dash_state for r in results] == [NOT_FOUND, COMPLETED]
    assert 5 * 60 <= portal.clk.t < 15 * 60   # did not wait the whole 15 minutes
    assert any("the portal did not take this upload" in line for line in portal.logs)


def test_nothing_found_at_all_waits_the_full_time():
    portal = FakeDashPortal([[OLD]])
    results = [up(INVENTORY), up(PRICE)]
    portal.verify(results, {"old"})
    assert [r.dash_state for r in results] == [NOT_FOUND, NOT_FOUND]
    assert portal.clk.t >= 15 * 60


def test_dashboard_that_never_loads_stops():
    portal = FakeDashPortal([RuntimeError("net::ERR_CONNECTION_CLOSED")], {"dashboard_load_failures": 3})
    with pytest.raises(PortalError, match="did not load 3 times"):
        portal.verify([up(PRICE)], {"old"})
