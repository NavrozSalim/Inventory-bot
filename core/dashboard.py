"""Import Request Dashboard: read rows and match them to this run's uploads.

Rows are newest first: ID | Description | Requested Date / Time | Status | Action.
The table does not show file names, so rows are matched by type ("Import Stock/Status Request" or
"Import Price Request") and order: the IDs seen before uploading are ignored, and the newest N rows of each
type belong to this run's N uploads of that type, oldest row to first upload.
"""
import re
from dataclasses import dataclass

from .files import INVENTORY, PRICE
from .report import COMPLETED, DASH_FAILED, ERRORS, NOT_FOUND, PENDING, SUBMITTED

FINAL_STATES = (COMPLETED, ERRORS, DASH_FAILED)
ERRORS_RE = re.compile(r"(\d+)\s*ERRORS?\b", re.I)
HEX_ID_RE = re.compile(r"^[0-9a-f]{12,}$", re.I)
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.I)
IMPORT_RE = re.compile(r"\bimport\b", re.I)
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
STATUS_RE = re.compile(r"COMPLETED|PENDING|PROCESSING|IN PROGRESS|QUEUED|FAILED|ERROR", re.I)
CLOCK_SLACK_SECONDS = 300   # the laptop clock may be a few minutes off the portal's


@dataclass
class DashRow:
    request_id: str
    description: str
    requested_at: str
    status: str
    text: str = ""

    @property
    def kind(self):
        d = self.description.lower()
        if "price" in d:
            return PRICE
        if "stock" in d:
            return INVENTORY
        return None

    @property
    def state(self):
        return row_state(self)[0]


def _first(cells, pattern):
    return next((c for c in cells if pattern.search(c)), None)


def parse_row(cells, row_text=""):
    """A dashboard row from its cells, found by content so a header cell or an extra column does not shift them."""
    cells = [(c or "").strip() for c in cells]
    if len(cells) < 4:
        return None
    rid = _first(cells, HEX_ID_RE)
    desc = _first(cells, IMPORT_RE)
    if rid is None and desc is None:
        return None   # header row or something else
    rid = rid if rid is not None else cells[0]
    if not rid:
        return None
    when = _first(cells, DATE_RE) or cells[2]
    status = _first(cells, STATUS_RE) or cells[3]
    return DashRow(rid, desc if desc is not None else cells[1], when, status, row_text or " ".join(cells))


def id_time(request_id):
    """Creation time (Unix seconds) of a 24-hex-character request ID, which starts with it; else None."""
    if not OBJECT_ID_RE.match(request_id or ""):
        return None
    return int(request_id[:8], 16)


def row_state(row):
    """(state, error count) from the status cell and the rest of the row."""
    text = f"{row.status} {row.text}".upper()
    m = ERRORS_RE.search(text)
    errors = int(m.group(1)) if m else 0
    if "FAILED" in text:
        return DASH_FAILED, errors
    if errors > 0:
        return ERRORS, errors
    if "COMPLETED" in text:
        return COMPLETED, 0
    return PENDING, 0


def new_rows(rows, known_ids, since=None):
    """Rows above the first one seen before the run. With since (Unix time of the first upload), rows whose ID
    shows they were created before it are left out too, so older rows are not taken when the snapshot was
    empty or missed them."""
    out = []
    for row in rows:
        if known_ids and row.request_id in known_ids:
            break
        created = id_time(row.request_id)
        if since is not None and created is not None and created < since - CLOCK_SLACK_SECONDS:
            continue
        out.append(row)
    return out


def match_results(results, rows):
    """Fill request id, time and state on each submitted upload. Returns the number still open."""
    still_open = 0
    for kind in (INVENTORY, PRICE):
        subs = [r for r in results if r.status == SUBMITTED and r.item.kind == kind]
        cand = [row for row in rows if row.kind == kind][:len(subs)]
        cand.reverse()   # oldest first, like the uploads
        for r, row in zip(subs, cand):
            state, errors = row_state(row)
            r.request_id, r.requested_at, r.dash_state, r.error_count = (
                row.request_id, row.requested_at, state, errors)
        for r in subs[len(cand):]:
            r.request_id, r.requested_at, r.dash_state, r.error_count = "", "", NOT_FOUND, 0
        still_open += sum(1 for r in subs if r.dash_state not in FINAL_STATES)
    return still_open
