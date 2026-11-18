"""The bot's own Chrome profile (same approach as the Order bot).

The profile folder is chrome-profiles/<chrome_profile> (or user_data_dir). Sign in to Woolworths MarketPlus there
once with `python open_profile.py`. The bot never types a password. When a Chrome is already open on the
debug port the bot attaches to it and leaves it open; otherwise it starts Chrome on the folder and closes it at the end.
"""
import json
import os
import shutil
import urllib.request
from pathlib import Path

from .config import ROOT


class BrowserStartError(Exception):
    pass


def profile_dir(cfg):
    return Path(cfg["user_data_dir"]) if cfg.get("user_data_dir") else ROOT / "chrome-profiles" / cfg["chrome_profile"]


def chrome_exe(cfg):
    for c in [cfg.get("chrome_path", ""), os.environ.get("CHROME_PATH", ""), shutil.which("chrome") or "",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]:
        if c and Path(c).exists():
            return c
    raise BrowserStartError("Chrome not found; set chrome_path in config.json")


def _port_up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5).read()
        return True
    except Exception:
        return False


def keep_session_cookies(cfg):
    """"Continue where you left off", so sign-in cookies without an expiry survive a restart."""
    prefs = profile_dir(cfg) / (cfg.get("profile_directory") or "Default") / "Preferences"
    try:
        data = json.loads(prefs.read_text(encoding="utf-8-sig")) if prefs.exists() else {}
    except Exception:
        return
    session = data.setdefault("session", {})
    if session.get("restore_on_startup") == 1:
        return
    session["restore_on_startup"] = 1
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text(json.dumps(data), encoding="utf-8")


class BrowserSession:
    def __init__(self, cfg):
        self.cfg = cfg
        self._pw = None
        self._owns = False
        self.context = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        cfg = self.cfg
        port = int(cfg.get("debug_port") or 9295)
        self._pw = sync_playwright().start()
        try:
            if _port_up(port):
                try:
                    browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                except Exception as e:
                    raise BrowserStartError(
                        f"A Chrome is open on port {port} but could not be attached ({type(e).__name__}). "
                        "Close that Chrome window and run again.") from None
                self.context = browser.contexts[0]
                return self
            pdir = profile_dir(cfg)
            pdir.mkdir(parents=True, exist_ok=True)
            keep_session_cookies(cfg)
            args = ["--no-first-run", "--no-default-browser-check",
                    f"--profile-directory={cfg.get('profile_directory') or 'Default'}"]
            try:
                self.context = self._pw.chromium.launch_persistent_context(
                    str(pdir), executable_path=chrome_exe(cfg), headless=False, no_viewport=True,
                    accept_downloads=True, args=args, ignore_default_args=["--enable-automation"])
            except BrowserStartError:
                raise
            except Exception as e:
                raise BrowserStartError(
                    f"Could not start Chrome on profile {pdir} ({type(e).__name__}: {str(e)[:200]}). "
                    "Close any Chrome window using this profile and try again.") from None
            self._owns = True
            return self
        except BaseException:
            self._pw.stop()
            self._pw = None
            raise

    def __exit__(self, *exc):
        try:
            if self._owns and self.context is not None:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    def new_page(self):
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(60000)
        return page
