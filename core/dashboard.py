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


def parse_row(cells, row_text=""):
    cells = [(c or "").strip() for c in cells]
    if len(cells) < 4 or not cells[0]:
        return None
    return DashRow(cells[0], cells[1], cells[2], cells[3], row_text or " ".join(cells))


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


def new_rows(rows, known_ids):
    """Rows above the first one seen before the run (all rows when none was seen)."""
    out = []
    for row in rows:
        if known_ids and row.request_id in known_ids:
            break
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
