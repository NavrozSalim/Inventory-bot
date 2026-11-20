"""Settings from config.json (copy config.example.json). Missing keys fall back to DEFAULTS."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "<"

PORTAL_HOST = "portal.woolworthsmarketplus.com.au"

DEFAULTS = {
    "softether": {
        # Blank vpncmd_path: look in the usual SoftEther VPN Client folders.
        "vpncmd_path": "",
        # Blank account_name: use the saved connection whose server is expected_ip.
        "account_name": "AU-PIY",
        "connect_timeout_seconds": 60,
    },
    "expected_ip": "103.100.38.134",
    "ip_check_url": "https://nordvpn.com/ip-lookup/",
    "ip_check_timeout_seconds": 30,
    "ip_check_attempts": 3,              # tries of the IP lookup page before the run stops
    "seconds_between_ip_checks": 10,
    "chrome_profile": "MyDeal Seller",
    "user_data_dir": "",
    "profile_directory": "",
    "debug_port": 9295,
    "chrome_path": "",
    "org_name": "Prettyandpractical Pty Ltd",
    "portal": {
        "home_url": f"https://{PORTAL_HOST}/dashboard",
        # Used only when the Inventory -> Manage Inventory / Import Request Dashboard clicks do not work.
        "manage_inventory_url": f"https://{PORTAL_HOST}/inventory/manage-inventory",
        "import_dashboard_url": f"https://{PORTAL_HOST}/inventory/import-request-dashboard",
        "inventory_card_text": "Import File to Update Stock/Status",
        "price_card_text": "Import File to Update Price",
    },
    "upload_order": "vendor",            # "vendor": each vendor's Inventory then Price; "kind": all Inventory, then all Price
    "seconds_between_uploads": 5,
    "upload_attempts": 3,                # tries per file: again when the portal refuses it or the dashboard shows it failed
    "seconds_between_attempts": 15,
    "upload_feedback_seconds": 30,
    "wait_after_upload_minutes": 3,
    "dashboard_extra_wait_minutes": 15,
    "missing_row_wait_minutes": 5,       # an upload with no dashboard row this long after the first check is uploaded again
    "dashboard_load_failures": 10,       # dashboard page failing to load this many checks in a row ends the check
    "dashboard_refresh_seconds": 30,
    "human_wait_minutes": 10,
    "max_rows_per_file": 0,              # 0 = never split; e.g. 2000 splits bigger vendor files into parts
}


class ConfigError(Exception):
    pass


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    path = Path(path) if path else ROOT / "config.json"
    if not path.exists():
        raise ConfigError(f"{path.name} not found. Copy config.example.json to config.json and check the settings.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise ConfigError(f"{path.name} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must hold one JSON object.")
    return _merge(DEFAULTS, data)


def is_set(value):
    return bool(str(value or "").strip()) and not str(value).strip().startswith(PLACEHOLDER)
