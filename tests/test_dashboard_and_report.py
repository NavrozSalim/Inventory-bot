from datetime import datetime
from pathlib import Path

from core.dashboard import DashRow, match_results, new_rows, parse_row, row_state
from core.files import INVENTORY, PRICE, UploadItem
from core.portal import classify_feedback, login_state
from core.report import (COMPLETED, DASH_FAILED, DRY_RUN, ERRORS, FAILED, NOT_FOUND, PENDING, REJECTED, SUBMITTED,
                         UploadResult, all_ok, result_line, summary, write_csv)

HOST = "https://portal.woolworthsmarketplus.com.au"


def row(rid, desc, status, when="18/11/2026 14:05"):
    return DashRow(rid, desc, when, status, f"{rid} {desc} {when} {status} View Result")


def submitted(vendor, kind):
    item = UploadItem(vendor, kind, Path(f"{vendor}-{kind}.csv"), 10)
    return UploadResult(item=item, status=SUBMITTED, uploaded_at=datetime(2026, 11, 18, 14, 5))


def test_parse_row_and_kind():
    r = parse_row(["6af1", "Import Price Request", "17/11/2026 06:58", "COMPLETED", "View Result"])
    assert (r.request_id, r.kind, r.state) == ("6af1", PRICE, COMPLETED)
    assert parse_row(["", "x", "y", "z"]) is None
    assert parse_row(["only", "three", "cells"]) is None
    assert row("1", "Import Stock/Status Request", "COMPLETED").kind == INVENTORY
    assert row("1", "Import product create/update request", "COMPLETED").kind is None


def test_row_states():
    assert row_state(row("1", "Import Price Request", "COMPLETED 1 ERRORS")) == (ERRORS, 1)
    assert row_state(row("1", "Import Price Request", "IN PROGRESS")) == (PENDING, 0)
    assert row_state(row("1", "Import Price Request", "FAILED")) == (DASH_FAILED, 0)


def test_new_rows_stop_at_first_known_id():
    rows = [row("n2", "Import Price Request", "COMPLETED"), row("n1", "Import Price Request", "COMPLETED"),
            row("old", "Import Price Request", "COMPLETED"), row("older", "Import Price Request", "COMPLETED")]
    assert [r.request_id for r in new_rows(rows, {"old", "older"})] == ["n2", "n1"]
    assert len(new_rows(rows, set())) == 4


def test_match_by_type_oldest_row_to_first_upload():
    results = [submitted("Amazon AU", INVENTORY), submitted("Amazon AU", PRICE),
               submitted("Vevor", INVENTORY), submitted("Vevor", PRICE)]
    rows = [  # newest first
        row("p2", "Import Price Request", "PROCESSING"),
        row("i2", "Import Stock/Status Request", "COMPLETED 2 ERRORS"),
        row("p1", "Import Price Request", "COMPLETED"),
        row("i1", "Import Stock/Status Request", "COMPLETED"),
        row("x", "Import product create/update request", "COMPLETED"),
    ]
    assert match_results(results, rows) == 1
    got = {(r.item.vendor, r.item.kind): (r.request_id, r.dash_state, r.error_count) for r in results}
    assert got[("Amazon AU", INVENTORY)] == ("i1", COMPLETED, 0)
    assert got[("Vevor", INVENTORY)] == ("i2", ERRORS, 2)
    assert got[("Amazon AU", PRICE)] == ("p1", COMPLETED, 0)
    assert got[("Vevor", PRICE)] == ("p2", PENDING, 0)


def test_missing_rows_are_not_found_and_failed_uploads_ignored():
    results = [submitted("A", PRICE), submitted("B", PRICE)]
    refused = submitted("C", PRICE)
    refused.status = REJECTED
    results.append(refused)
    assert match_results(results, [row("p1", "Import Price Request", "COMPLETED")]) == 1
    assert [r.dash_state for r in results] == [COMPLETED, NOT_FOUND, ""]


def test_result_lines_and_summary(tmp_path):
    a = submitted("Amazon AU", INVENTORY)
    a.request_id, a.requested_at, a.dash_state = "6af1", "18/11/2026 14:06", COMPLETED
    b = submitted("Vevor", PRICE)
    b.request_id, b.dash_state, b.error_count = "6af2", ERRORS, 1
    c = submitted("Costco", INVENTORY)
    c.status, c.message = FAILED, "TimeoutError: no box"
    assert result_line(a) == "Inventory updated: Amazon AU at 18/11/2026 14:06, COMPLETED, request 6af1"
    assert result_line(b).startswith("Price updated with 1 error(s): Vevor at 18/11/2026 14:05")
    assert result_line(c) == "Inventory upload failed: Costco - TimeoutError: no box"
    assert summary([a, b, c]) == ("Finished: 3 vendor(s), 3 file(s): 1 completed, 1 completed with errors, "
                                  "0 still processing, 1 failed or not found.")
    assert not all_ok([a, b]) and all_ok([a])
    out = write_csv(tmp_path / "run.csv", [a, b, c]).read_text(encoding="utf-8").splitlines()
    assert out[0].startswith("vendor,file_type,part") and "Amazon AU,Stock/Status,1/1" in out[1]


def test_dry_run_summary():
    r = submitted("Vevor", PRICE)
    r.status = DRY_RUN
    assert summary([r]).startswith("Dry run finished: 1 file(s)")
    assert all_ok([r])


def test_classify_feedback():
    assert classify_feedback("Import request submitted successfully") == SUBMITTED
    assert classify_feedback("") == SUBMITTED
    assert classify_feedback("Invalid file format") == REJECTED
    assert classify_feedback("File size exceeds the limit") == REJECTED


def test_login_state():
    def st(url, body, pw=False, code=False):
        return login_state(url, body, org_name="Prettyandpractical Pty Ltd", has_password=pw, has_code_input=code)

    assert st("https://auth.example/org", "x@gmail.com is a part of multiple organizations. "
              "Prettyandpractical Pty Ltd OR Continue with personal account") == "org"
    assert st(f"{HOST}/dashboard", "Prettyandpractical Pty Ltd Home Inventory Orders Welcome back") == "ready"
    assert st("https://auth.example/mfa", "We sent a code to your email. Enter the code") == "code"
    assert st("https://auth.example/x", "", code=True) == "code"
    assert st("https://auth.example/u/login", "Password", pw=True) == "password"
    assert st("https://auth.example/u/login", "Sign in") == "login"
    assert st(f"{HOST}/dashboard", "Loading") == "loading"
