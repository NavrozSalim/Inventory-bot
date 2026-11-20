# MyDeal inventory bot

Uploads the MyDeal Stock/Status and Price files to the Woolworths MarketPlus seller portal, **one vendor at a
time** (MyDeal sometimes refuses one big file), then checks the Import Request Dashboard and logs each vendor's
result.

What one run does:

1. Connects SoftEther VPN connection **AU-PIY** (server `103.100.38.134`) with `vpncmd`.
2. Opens the bot's Chrome profile and checks on the NordVPN IP lookup page that the IP is `103.100.38.134`.
   The page is checked up to 3 times, 10 s apart (`ip_check_attempts`, `seconds_between_ip_checks`).
   Still the wrong IP, no IP or no VPN after that: the run stops before anything is uploaded.
3. Opens the portal. On the "part of multiple organizations" page it clicks **Prettyandpractical Pty Ltd**.
   If a verification-code or password page shows, it beeps, brings Chrome to the front and waits (10 minutes)
   for you to type it in the Chrome window. The bot never types a password or a code.
4. Inventory > Manage Inventory: for each vendor, uploads the Stock/Status file (Import File to Update
   Stock/Status > Import), then the Price file (Import File to Update Price > Import).
5. Waits 3 minutes, opens the Import Request Dashboard and matches each upload to its row. Rows not yet
   COMPLETED are re-checked every 30 s for up to 15 more minutes. Rows with errors get a screenshot of
   View Result.
   Each file gets up to 3 tries: a file the portal refuses, or that FAILED or never showed up on the
   dashboard, is uploaded again (then the dashboard is checked again).
6. Logs one line per file, e.g. `Inventory updated: Amazon AU at 18/11/2026 14:05, COMPLETED, request 6af…`.

## Setup on a new laptop (one command)

Needs Git, Python 3.11+ (on PATH), Google Chrome and SoftEther VPN Client with the AU-PIY connection. In PowerShell:

```
git clone https://github.com/NavrozSalim/Inventory-bot.git "$HOME\Inventory-bot"; cd "$HOME\Inventory-bot"; .\setup.bat
```

`setup.bat` installs the Python packages, creates `config.json` and `input\`, and checks Chrome, SoftEther and the
VPN connection. It prints `[!!]` for anything missing. Update later with `git pull` then `.\setup.bat`.

Then sign in once (VPN on):

```
python open_profile.py
```

`open_profile.py` opens Chrome on the bot's own profile (`chrome-profiles/MyDeal Seller`). Sign in to Woolworths
MarketPlus there once, pick the organisation, enter the code, then press Enter in the console. Do this with the
VPN on, so the portal sees the usual IP.

Settings in `config.json` (all optional except what you want to change):

| Key | Meaning |
| --- | --- |
| `softether.account_name` | SoftEther connection to connect (`AU-PIY`). Blank = the one whose server is `expected_ip`. |
| `expected_ip` | IP the NordVPN page must show (`103.100.38.134`). |
| `chrome_profile` / `user_data_dir` | The bot's Chrome profile folder. |
| `org_name` | Organisation to click on the sign-in page. |
| `portal.*_url` | Fallback page addresses, used only when the menu clicks do not work. |
| `upload_order` | `vendor` (each vendor's Stock/Status then Price) or `kind` (all Stock/Status, then all Price). |
| `wait_after_upload_minutes` | Wait before the first dashboard check (3). |
| `dashboard_extra_wait_minutes` | How long to keep re-checking rows that are not finished (15). |
| `max_rows_per_file` | Split a bigger vendor file into parts of this many rows (0 = never). |
| `upload_attempts` | Tries per file (3). A file is uploaded again when the portal refuses it or the page fails, and when the Import Request Dashboard shows it FAILED or it never shows up there. |
| `seconds_between_attempts` | Wait before uploading a file again (15). |
| `missing_row_wait_minutes` | When other uploads are on the dashboard but one never shows up, give up on it after this many minutes and upload it again (5). |

## Running from the web app (recommended)

The MyDeal store's Catalog page has **Upload the file on the Marketplace**. Pick the vendors, Inventory and/or
Price, optionally Dry run, and click Start. This laptop picks the job up, downloads exactly those files (built
with the latest prices and stock), runs the bot, and the web page shows the status, "Action needed on the laptop"
(e.g. type the verification code), each file's result and the log. Cancel stops the bot here.

One-time:

1. On the main server, create a token for the store owner (prints the token once):
   `python manage.py create_ingest_token --label inventory-laptop --scopes inventory-bot --owner-email <owner email>`
2. On this laptop, put the web app address and that token in `agent.json` (setup.bat creates it), then run
   `.\setup.bat` again: it checks the web app accepts the token.
3. Double-click `run_agent.bat` and leave the window open.

## Running by hand

1. In the web app, Catalog > MyDeal store > **Download templates > All vendors, separate files (ZIP)**.
2. Put the ZIP in `input\` (the bot takes the newest ZIP there), or pass it with `--zip`.
3. Double-click `run_inventory.bat`, or:

```
python run_inventory.py
python run_inventory.py --zip "C:\Users\me\Downloads\Mydeal - BW-P&P - Templates by Vendor.zip"
python run_inventory.py --dry-run                 everything except clicking Import
python run_inventory.py --only "Amazon AU"        one vendor (repeat --only for more)
python run_inventory.py --include-unmapped        also upload the Unmapped SKUs files (skipped by default)
python run_inventory.py --skip-vpn                VPN already on; the IP check still runs
```

**Do the first run with `--dry-run` and watch the Chrome window.** The page selectors were written from
screenshots, without access to the portal; any step that does not match stops with a message and a screenshot
in `logs\screenshots\<run>\`.

The Unmapped files are skipped by default: their price cells are blank and some rows mark products Discontinued.

## Output

* `logs\inventory-bot-<date>.log` — every step, plus the result line per file.
* `logs\inventory-run-<run>.csv` — vendor, file type, rows, upload time, portal message, request id, dashboard
  status, error count, screenshots.
* `logs\screenshots\<run>\` — after each Import, the dashboard, and View Result for rows with errors.

Exit code: 0 = every file COMPLETED (or dry run), 1 = some file failed, has errors or is still processing,
2 = stopped before or during uploading (VPN, IP, sign-in, files).

How dashboard rows are matched: the table does not show file names. The bot notes the request IDs on the dashboard
before uploading; afterwards the new rows of each type ("Import Stock/Status Request", "Import Price Request")
belong to this run's uploads of that type, oldest row to first upload.

## Tests

```
python -m pytest
```

The tests cover the VPN commands (vpncmd mocked), the IP check, reading the ZIP, matching dashboard rows and the
log lines. No browser or portal is opened.
