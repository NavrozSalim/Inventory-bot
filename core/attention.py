"""Get the attention of the person at the laptop (verification code, sign-in)."""
import sys


def beep(times=3):
    if not sys.platform.startswith("win"):
        return
    try:
        import winsound

        for _ in range(times):
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def alert(page, text, log):
    log(f"ACTION NEEDED: {text}")
    try:
        page.bring_to_front()
    except Exception:
        pass
    beep()
