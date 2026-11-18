"""Get the attention of the person at the laptop (verification code, sign-in).

When the agent runs the bot, it sets INVENTORY_BOT_ATTENTION_FILE; the message written there is shown in the web
app as "Action needed on the laptop" until it is cleared.
"""
import os
import sys
from pathlib import Path

ATTENTION_ENV = "INVENTORY_BOT_ATTENTION_FILE"


def beep(times=3):
    if not sys.platform.startswith("win"):
        return
    try:
        import winsound

        for _ in range(times):
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def tell_web_app(text):
    path = os.environ.get(ATTENTION_ENV)
    if not path:
        return
    try:
        Path(path).write_text((text or "").strip()[:300], encoding="utf-8")
    except OSError:
        pass


def clear():
    tell_web_app("")


def alert(page, text, log):
    log(f"ACTION NEEDED: {text}")
    tell_web_app(text)
    try:
        page.bring_to_front()
    except Exception:
        pass
    beep()
