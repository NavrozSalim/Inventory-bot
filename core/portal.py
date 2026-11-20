"""Woolworths MarketPlus seller portal (MyDeal): sign in, upload each vendor file, check the dashboard.

The bot never types a password or a verification code. On the "part of multiple organizations" page it clicks
the organisation in config.json (org_name). When a code or password page shows, it beeps, brings Chrome to the
front and waits (human_wait_minutes) until the dashboard opens.

The page selectors are best-effort, written from screenshots. Do the first run with --dry-run and watch.
"""
import re
import time
from datetime import datetime
from pathlib import Path

from .attention import alert
from .attention import clear as clear_attention
from .config import PORTAL_HOST, is_set
from .dashboard import match_results, new_rows, parse_row
from .files import INVENTORY
from .report import DASH_FAILED, DRY_RUN, ERRORS, FAILED, NOT_FOUND, REJECTED, SUBMITTED, UploadResult

FEEDBACK_SELECTOR = ("[role=alert], [role=status], .MuiSnackbar-root, .MuiAlert-message, "
                     "[class*='toast' i], [class*='snackbar' i], [class*='notification' i], [role=dialog]")
OK_WORDS = re.compile(r"success|submitted|received|queued|accepted|uploaded", re.I)
BAD_WORDS = re.compile(r"error|fail|invalid|too large|unable|rejected|not allowed|exceed", re.I)
CODE_WORDS = re.compile(r"verification code|enter (the |your )?code|one[- ]time|security code|we sent|sent a code",
                        re.I)
LOGIN_URL_PARTS = ("login", "signin", "sign-in", "/auth", "authorize", "/u/", "identifier")
ROW_SELECTOR = "table tbody tr, [role=rowgroup] [role=row], [role=grid] [role=row]"
CELL_SELECTOR = "td, th, [role=cell], [role=gridcell]"


class PortalError(Exception):
    pass


def classify_feedback(message):
    """REJECTED when the portal's message reads as an error and not as a success."""
    if message and BAD_WORDS.search(message) and not OK_WORDS.search(message):
        return REJECTED
    return SUBMITTED


def login_state(url, body, *, org_name, has_password, has_code_input):
    """org | ready | code | password | login | loading."""
    low = (body or "").lower()
    url = (url or "").lower()
    if org_name.lower() in low and ("multiple organi" in low or "continue with personal account" in low):
        return "org"
    on_portal = PORTAL_HOST in url and not has_password
    if on_portal and ("manage inventory" in low or ("inventory" in low and "orders" in low)):
        return "ready"
    if has_code_input or CODE_WORDS.search(low):
        return "code"
    if has_password:
        return "password"
    if any(p in url for p in LOGIN_URL_PARTS):
        return "login"
    return "loading"


class Portal:
    def __init__(self, page, cfg, log, shots_dir, *, clock=time.monotonic, on_change=None):
        self.on_change = on_change or (lambda: None)   # called when dashboard results change
        self.page = page
        self.cfg = cfg
        self.pcfg = cfg["portal"]
        self.log = log
        self.shots_dir = Path(shots_dir)
        self.clock = clock
        self.manage_url = ""
        self.dashboard_url = ""
        self.seen_ids = set()   # every request ID read on the dashboard so far
        self._shot_n = 0

    # ---------- helpers ----------
    def screenshot(self, name):
        self._shot_n += 1
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        path = self.shots_dir / f"{self._shot_n:02d}-{re.sub(r'[^A-Za-z0-9_-]+', '_', name)[:60]}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return ""

    def _body(self):
        try:
            return self.page.inner_text("body", timeout=3000)
        except Exception:
            return ""

    def _count(self, selector):
        try:
            return self.page.locator(selector).count()
        except Exception:
            return 0

    def _click_text(self, text, timeout=8000):
        """Click a visible button/link/menu item with this text."""
        page = self.page
        pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
        for loc in (page.get_by_role("button", name=pattern), page.get_by_role("link", name=pattern),
                    page.get_by_role("menuitem", name=pattern), page.get_by_text(text, exact=True)):
            try:
                n = loc.count()
            except Exception:
                n = 0
            for i in range(n):
                el = loc.nth(i)
                try:
                    if el.is_visible():
                        el.click(timeout=timeout)
                        return True
                except Exception:
                    continue
        return False

    # ---------- sign in ----------
    def state(self):
        return login_state(
            self.page.url, self._body(), org_name=self.cfg["org_name"],
            has_password=self._count("input[type=password]:visible") > 0,
            has_code_input=self._count("input[autocomplete='one-time-code']") > 0)

    def sign_in(self):
        page, org = self.page, self.cfg["org_name"]
        self.log(f"Opening {self.pcfg['home_url']} ...")
        page.goto(self.pcfg["home_url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        wait_s = float(self.cfg.get("human_wait_minutes", 10)) * 60
        start, human_deadline, alerted, org_clicks = self.clock(), None, set(), 0
        while True:
            state = self.state()
            if state == "ready":
                if alerted:
                    clear_attention()
                self.log("Signed in to Woolworths MarketPlus.")
                return
            if state == "org":
                if org_clicks >= 3:
                    raise PortalError(f"Clicked '{org}' 3 times but the organisation page is still showing.")
                pattern = re.compile(re.escape(org), re.I)
                target = page.get_by_role("button", name=pattern)
                if target.count() == 0:
                    target = page.get_by_text(org, exact=False)
                target.first.click()
                org_clicks += 1
                self.log(f"Picked the organisation '{org}'.")
                page.wait_for_timeout(3000)
                continue
            if state in ("code", "password", "login") and state not in alerted:
                alerted.add(state)
                human_deadline = human_deadline or self.clock() + wait_s
                msg = {
                    "code": "Type the verification code in the Chrome window. The bot carries on once the "
                            "dashboard opens.",
                    "password": "Sign in to Woolworths MarketPlus in the Chrome window. The bot never types "
                                "passwords.",
                    "login": "Sign in to Woolworths MarketPlus in the Chrome window.",
                }[state]
                alert(page, f"{msg} Waiting up to {wait_s / 60:.0f} minutes.", self.log)
            if state == "loading" and human_deadline is None and self.clock() - start > 90:
                human_deadline = self.clock() + wait_s
                alert(page, f"The portal has not reached the dashboard (now at {page.url}). Please check the "
                            "Chrome window.", self.log)
            if human_deadline and self.clock() > human_deadline:
                self.screenshot("sign-in-timeout")
                raise PortalError("Sign-in did not finish in time (code or password not entered). "
                                  "Nothing was uploaded.")
            page.wait_for_timeout(3000)

    # ---------- manage inventory ----------
    def _card_text(self, kind):
        return self.pcfg["inventory_card_text"] if kind == INVENTORY else self.pcfg["price_card_text"]

    def _wait_manage_page(self, timeout_ms=30000):
        self.page.get_by_text(self.pcfg["inventory_card_text"]).first.wait_for(timeout=timeout_ms)

    def open_manage_inventory(self):
        page = self.page
        if self.manage_url:
            page.goto(self.manage_url, wait_until="domcontentloaded")
            self._wait_manage_page()
            return
        try:
            if not self._click_text("Manage Inventory", timeout=3000):
                self._click_text("Inventory")
                page.wait_for_timeout(800)
                self._click_text("Manage Inventory")
            self._wait_manage_page(20000)
        except Exception:
            url = self.pcfg.get("manage_inventory_url")
            if not is_set(url):
                raise PortalError("Could not open Inventory > Manage Inventory.") from None
            self.log(f"Menu click did not open Manage Inventory; going to {url}")
            page.goto(url, wait_until="domcontentloaded")
            try:
                self._wait_manage_page()
            except Exception:
                self.screenshot("manage-inventory-not-found")
                raise PortalError("Manage Inventory page did not show the 'Update Stock/Status' import box. "
                                  "Check portal.manage_inventory_url in config.json.") from None
        self.manage_url = page.url
        self.log(f"Manage Inventory page: {self.manage_url}")

    def _card(self, kind):
        text = self._card_text(kind)
        cards = self.page.locator("div, section, form").filter(has_text=text).filter(
            has=self.page.locator("input[type=file]"))
        n = cards.count()
        if n == 0:
            raise PortalError(f"The '{text}' box (with a file chooser) was not found on Manage Inventory.")
        return cards.nth(n - 1)   # innermost box that holds both the title and the file chooser

    def _feedback_texts(self):
        try:
            return [t.strip() for t in self.page.locator(FEEDBACK_SELECTOR).all_inner_texts() if t.strip()]
        except Exception:
            return []

    def _feedback(self, seconds, already_shown=()):
        """The portal's message after Import. Notices already on the page before the click (such as the
        "We've simplified your CSV template" banner) are not the answer to this upload."""
        old = set(already_shown)
        end = self.clock() + seconds
        while self.clock() < end:
            texts = [t for t in self._feedback_texts() if t not in old]
            if texts:
                return " | ".join(dict.fromkeys(texts))[:300]
            self.page.wait_for_timeout(500)
        return ""

    def _close_dialogs(self):
        for name in ("OK", "Close", "Got it", "Done"):
            try:
                btn = self.page.locator("[role=dialog]").get_by_role("button", name=re.compile(rf"^\s*{name}\s*$", re.I))
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=3000)
                    return
            except Exception:
                continue

    def upload(self, item, *, dry_run=False):
        result = UploadResult(item=item)
        clicked = False
        try:
            self.open_manage_inventory()
            card = self._card(item.kind)
            card.scroll_into_view_if_needed()
            file_input = card.locator("input[type=file]").first
            file_input.set_input_files(str(item.path))
            self.page.wait_for_timeout(800)
            if dry_run:
                result.screenshots.append(self.screenshot(f"dry-run-{item.label}"))
                file_input.set_input_files([])
                result.status, result.message = DRY_RUN, "file chosen, Import not clicked"
                return result
            button = card.get_by_role("button", name=re.compile(r"^\s*import\s*$", re.I))
            if button.count() == 0:
                button = card.locator("button:has-text('Import')")
            if button.count() == 0:
                raise PortalError(f"No Import button in the '{self._card_text(item.kind)}' box.")
            button = button.first
            for _ in range(20):   # the button may stay disabled until the file is read
                if button.is_enabled():
                    break
                self.page.wait_for_timeout(250)
            shown_before = self._feedback_texts()
            result.uploaded_at = datetime.now()
            button.click()
            clicked = True
            message = self._feedback(float(self.cfg.get("upload_feedback_seconds", 30)), shown_before)
            result.message = message or "(no message shown)"
            result.status = classify_feedback(message)
            result.screenshots.append(self.screenshot(f"after-import-{item.label}"))
            self._close_dialogs()
        except Exception as e:
            # After Import was clicked the request may be on the dashboard, so it is checked there instead of
            # being uploaded again straight away.
            result.status = SUBMITTED if clicked else FAILED
            result.message = f"{'Import clicked, then ' if clicked else ''}{type(e).__name__}: {e}"[:300]
            result.screenshots.append(self.screenshot(f"failed-{item.label}"))
        return result

    # ---------- import request dashboard ----------
    def open_dashboard(self):
        page = self.page
        if self.dashboard_url:
            page.goto(self.dashboard_url, wait_until="domcontentloaded")
        else:
            opened = False
            try:
                self.open_manage_inventory()
                opened = self._click_text("Import Request Dashboard")
            except Exception:
                opened = False
            if not opened:
                url = self.pcfg.get("import_dashboard_url")
                if not is_set(url):
                    raise PortalError("Could not open the Import Request Dashboard.")
                page.goto(url, wait_until="domcontentloaded")
        try:
            page.get_by_text("Import Request Dashboard").first.wait_for(timeout=30000)
            page.locator("table").first.wait_for(timeout=30000)
        except Exception:
            self.screenshot("dashboard-not-found")
            raise PortalError("The Import Request Dashboard did not load. Check portal.import_dashboard_url "
                              "in config.json.") from None
        self._refresh_dashboard()
        try:
            page.locator(ROW_SELECTOR).first.wait_for(timeout=10000)
        except Exception:
            pass   # an empty dashboard is fine
        if not self.dashboard_url:
            self.dashboard_url = page.url
            self.log(f"Import Request Dashboard: {self.dashboard_url}")

    def _refresh_dashboard(self):
        """The dashboard says new requests show only after its Refresh button is clicked."""
        try:
            btn = self.page.get_by_role("button", name=re.compile(r"^\s*refresh\s*$", re.I))
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=5000)
                self.page.wait_for_timeout(2500)
        except Exception:
            pass

    def _rows_on_page(self):
        rows = []
        for tr in self.page.locator(ROW_SELECTOR).all():
            try:
                row = parse_row(tr.locator(CELL_SELECTOR).all_inner_texts(), tr.inner_text())
            except Exception:
                row = None
            if row and all(r.request_id != row.request_id for r in rows):
                rows.append(row)
        return rows

    def read_rows(self, known_ids=None, max_pages=5):
        rows = []
        for _ in range(max_pages):
            page_rows = self._rows_on_page()
            rows.extend(page_rows)
            if not known_ids or any(r.request_id in known_ids for r in page_rows):
                break
            nxt = self.page.locator("button[aria-label*='next page' i]")
            try:
                if nxt.count() == 0 or not nxt.first.is_enabled():
                    break
                nxt.first.click()
            except Exception:
                break
            self.page.wait_for_timeout(1500)
        return rows

    def snapshot_ids(self):
        """IDs already on the dashboard before uploading, so this run's rows can be told apart."""
        self.open_dashboard()
        ids = {r.request_id for r in self._rows_on_page()}
        self.log(f"Import Request Dashboard has {len(ids)} earlier request(s) on its first page.")
        return ids

    def _wait(self, seconds, label):
        end = self.clock() + seconds
        while self.clock() < end:
            left = end - self.clock()
            if label:
                self.log(f"{label}: {left / 60:.1f} minute(s) left")
            self.page.wait_for_timeout(int(min(60, max(1, left)) * 1000))

    def _read_dashboard(self, known_ids):
        """All rows on the dashboard, or None when it did not load (connection closed, empty page)."""
        try:
            self.open_dashboard()
            rows = self.read_rows(known_ids)
        except Exception as e:   # e.g. net::ERR_CONNECTION_CLOSED: try again on the next check
            self.log(f"Dashboard did not load: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
            return None
        if not rows and (known_ids or self.seen_ids):
            self.log("Dashboard showed no rows (still loading); not counted.")
            return None
        self.seen_ids.update(r.request_id for r in rows)
        return rows

    def verify(self, results, known_ids):
        """Wait, then check every submitted upload on the dashboard until done or time is up.

        An upload whose row has still not shown up missing_row_wait_minutes after the first check, while
        other uploads of the same check are on the dashboard, is counted as not found, so it can be
        uploaded again without waiting out the whole dashboard_extra_wait_minutes.
        """
        submitted = [r for r in results if r.status == SUBMITTED]
        if not submitted:
            return
        self._wait(float(self.cfg.get("wait_after_upload_minutes", 3)) * 60,
                   "Waiting before checking the Import Request Dashboard")
        start = self.clock()
        deadline = start + float(self.cfg.get("dashboard_extra_wait_minutes", 15)) * 60
        missing_wait = float(self.cfg.get("missing_row_wait_minutes", 5)) * 60
        refresh = float(self.cfg.get("dashboard_refresh_seconds", 30))
        max_load_failures = int(self.cfg.get("dashboard_load_failures", 10))
        times = [r.uploaded_at.timestamp() for r in submitted if r.uploaded_at]
        since = min(times) if times else None
        load_failures = 0
        while True:
            all_rows = self._read_dashboard(known_ids)
            if all_rows is None:
                load_failures += 1
                if load_failures >= max_load_failures:
                    raise PortalError(f"The Import Request Dashboard did not load {load_failures} times in a row.")
                if self.clock() > deadline:
                    self.log("Stopped waiting: the dashboard did not load.")
                    break
                self.page.wait_for_timeout(int(refresh * 1000))
                continue
            load_failures = 0
            rows = new_rows(all_rows, known_ids, since)
            top = all_rows[0] if all_rows else None
            self.log(f"Dashboard: {len(all_rows)} row(s) read, {len(rows)} new"
                     + (f"; newest {top.request_id} {top.description} {top.requested_at} {top.status}"
                        if top else ""))
            still_open = match_results(submitted, rows)
            missing = [r for r in submitted if r.dash_state == NOT_FOUND]
            if missing and any(r.request_id for r in submitted) and self.clock() - start >= missing_wait:
                still_open -= len(missing)
                for r in missing:
                    self.log(f"{r.item.label}: no request on the dashboard {missing_wait / 60:.0f} minute(s) "
                             "after the first check; the portal did not take this upload.")
            self.on_change()
            done = len(submitted) - still_open
            if not still_open:
                self.log(f"Dashboard check done: {len(submitted) - len(missing)} of {len(submitted)} "
                         "import request(s) found and finished.")
                break
            if self.clock() > deadline:
                self.log(f"Stopped waiting: {done} of {len(submitted)} import request(s) finished.")
                break
            self.log(f"{done} of {len(submitted)} import request(s) finished; checking again in {refresh:.0f} s.")
            self.page.wait_for_timeout(int(refresh * 1000))
        self.screenshot("import-request-dashboard")
        for r in submitted:
            if r.dash_state in (ERRORS, DASH_FAILED):
                r.screenshots.append(self._view_result(r))

    def _view_result(self, r):
        """Open View Result for a row with errors and screenshot it."""
        try:
            row = self.page.locator("table tbody tr").filter(has_text=r.request_id).first
            row.get_by_text(re.compile(r"view result", re.I)).first.click(timeout=5000)
            self.page.wait_for_timeout(2500)
            path = self.screenshot(f"result-{r.item.label}")
            self.page.keyboard.press("Escape")
            if self.page.url != self.dashboard_url:
                self.page.goto(self.dashboard_url, wait_until="domcontentloaded")
            return path
        except Exception:
            return ""
