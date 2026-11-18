"""Open the bot's Chrome profile on the seller portal so you can sign in once (the bot never types passwords).

  python open_profile.py
"""
import sys

from core.browser import BrowserSession
from core.config import load_config


def main():
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    with BrowserSession(cfg) as session:
        page = session.new_page()
        page.goto(cfg["portal"]["home_url"], wait_until="domcontentloaded")
        print(f"Chrome is open on the '{cfg['chrome_profile']}' profile.")
        print("Sign in to Woolworths MarketPlus (pick the organisation, enter the code), tick 'remember me' "
              "if offered, then press Enter here to close.")
        input()


if __name__ == "__main__":
    main()
