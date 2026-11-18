"""Run log (console + logs/inventory-bot-<date>.log) and the per-run CSV summary."""
import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Upload status
DRY_RUN = "dry_run"
SUBMITTED = "submitted"
REJECTED = "rejected"
FAILED = "failed"
# Import Request Dashboard state
COMPLETED = "completed"
ERRORS = "completed_with_errors"
DASH_FAILED = "failed"
PENDING = "pending"
NOT_FOUND = "not_found"


class RunLog:
    def __init__(self, log_dir, stamp):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"inventory-bot-{stamp[:8]}.log"
        self.stamp = stamp

    def __call__(self, msg):
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except OSError:
            pass


@dataclass
class UploadResult:
    item: object
    status: str = ""
    message: str = ""
    uploaded_at: datetime = None
    request_id: str = ""
    requested_at: str = ""
    dash_state: str = ""
    error_count: int = 0
    screenshots: list = field(default_factory=list)


def _when(r):
    if r.requested_at:
        return r.requested_at
    return f"{r.uploaded_at:%d/%m/%Y %H:%M}" if r.uploaded_at else ""


def result_line(r):
    it = r.item
    what = "Inventory" if it.kind == "inventory" else "Price"
    who = f"{it.vendor}{f' (part {it.part} of {it.parts})' if it.parts > 1 else ''}"
    req = f", request {r.request_id}" if r.request_id else ""
    if r.status == DRY_RUN:
        return f"Dry run (not imported): {who} {it.kind_label} file, {it.rows} rows"
    if r.status == REJECTED:
        return f"{what} upload refused by the portal: {who} - {r.message}"
    if r.status == FAILED:
        return f"{what} upload failed: {who} - {r.message}"
    if r.dash_state == COMPLETED:
        return f"{what} updated: {who} at {_when(r)}, COMPLETED{req}"
    if r.dash_state == ERRORS:
        return (f"{what} updated with {r.error_count} error(s): {who} at {_when(r)}{req}. "
                "Open View Result on the Import Request Dashboard.")
    if r.dash_state == DASH_FAILED:
        return f"{what} import FAILED: {who} at {_when(r)}{req}"
    if r.dash_state == PENDING:
        return f"{what} still processing: {who}, uploaded {_when(r)}{req}"
    if r.dash_state == NOT_FOUND:
        return f"{what} uploaded but not found on the Import Request Dashboard: {who}, uploaded {_when(r)}"
    return f"{what} uploaded: {who} at {_when(r)}"


def summary(results):
    n = len(results)
    done = sum(1 for r in results if r.dash_state == COMPLETED)
    errs = sum(1 for r in results if r.dash_state == ERRORS)
    bad = sum(1 for r in results if r.status in (REJECTED, FAILED) or r.dash_state in (DASH_FAILED, NOT_FOUND))
    pending = sum(1 for r in results if r.dash_state == PENDING)
    vendors = len({r.item.vendor for r in results})
    if results and all(r.status == DRY_RUN for r in results):
        return f"Dry run finished: {n} file(s) for {vendors} vendor(s) checked, nothing imported."
    return (f"Finished: {vendors} vendor(s), {n} file(s): {done} completed, {errs} completed with errors, "
            f"{pending} still processing, {bad} failed or not found.")


def all_ok(results):
    return bool(results) and all(r.status == DRY_RUN or r.dash_state == COMPLETED for r in results)


CSV_COLUMNS = ["vendor", "file_type", "part", "file", "rows", "uploaded_at", "upload_status", "portal_message",
               "request_id", "requested_at", "dashboard_status", "errors", "screenshots"]


def write_csv(path, results):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in results:
            it = r.item
            w.writerow([it.vendor, it.kind_label, f"{it.part}/{it.parts}", it.path.name, it.rows,
                        f"{r.uploaded_at:%Y-%m-%d %H:%M:%S}" if r.uploaded_at else "", r.status, r.message,
                        r.request_id, r.requested_at, r.dash_state, r.error_count, " ".join(r.screenshots)])
    return path
