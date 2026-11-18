"""Read the web app's "Templates by Vendor" ZIP (or a folder) into a list of files to upload.

The ZIP has one folder per vendor, each with
  Mydeal - <store> - <vendor> - Price Template.csv
  Mydeal - <store> - <vendor> - Inventory Template.csv
The file type is taken from the header row, not the name. The Unmapped folder is skipped unless asked for.
"""
import csv
import io
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

PRICE_HEADERS = ["DealID", "VariantID", "ExternalID", "SKU", "Options", "DealTitle", "Price(IncGST)", "RRP(IncGST)"]
INVENTORY_HEADERS = ["DealID", "VariantID", "ExternalID", "SKU", "Options", "DealTitle", "StockOnHand",
                     "Discontinued", "WMPApproved"]
INVENTORY = "inventory"
PRICE = "price"
UNMAPPED = "unmapped"
NAME_RE = re.compile(r"^Mydeal - (?P<store>.+) - (?P<vendor>.+) - (?:Price|Inventory) Template", re.I)


class FilesError(Exception):
    pass


@dataclass
class UploadItem:
    vendor: str
    kind: str          # inventory | price
    path: Path
    rows: int
    store: str = ""
    part: int = 1
    parts: int = 1

    @property
    def kind_label(self):
        return "Stock/Status" if self.kind == INVENTORY else "Price"

    @property
    def label(self):
        part = f" (part {self.part} of {self.parts})" if self.parts > 1 else ""
        return f"{self.vendor} {self.kind_label}{part}"


def kind_from_headers(headers):
    cleaned = [h.strip().lstrip("﻿") for h in headers if h is not None]
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    if cleaned == PRICE_HEADERS:
        return PRICE
    if cleaned == INVENTORY_HEADERS:
        return INVENTORY
    return None


def _read_csv(path):
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], []
    body = [r for r in rows[1:] if any((c or "").strip() for c in r)]
    return rows[0], body


def _safe(name):
    cleaned = "".join("-" if c in '\\/:*?"<>|' else c for c in name).strip(" .")
    return cleaned or "file"


def _extract_zip(zip_path, dest):
    """Extract only the CSV files, keeping one folder level (the vendor). No paths outside ``dest``."""
    out = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".csv"):
                continue
            parts = [p for p in info.filename.replace("\\", "/").split("/") if p and p not in (".", "..")]
            if not parts:
                continue
            folder = _safe(parts[-2]) if len(parts) > 1 else ""
            target = dest / folder / _safe(parts[-1]) if folder else dest / _safe(parts[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            out.append((target, folder))
    return out


def _vendor_and_store(path, folder):
    m = NAME_RE.match(path.stem)
    store = m.group("store").strip() if m else ""
    vendor = folder or (m.group("vendor").strip() if m else "")
    return vendor or path.stem, store


def _split(item, header, body, max_rows, parts_dir):
    """Split one big file into parts of at most ``max_rows`` data rows."""
    chunks = [body[i:i + max_rows] for i in range(0, len(body), max_rows)]
    out = []
    for n, chunk in enumerate(chunks, start=1):
        target = parts_dir / f"{item.path.stem} - part {n} of {len(chunks)}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(chunk)
        target.write_text(buf.getvalue(), encoding="utf-8")
        out.append(UploadItem(item.vendor, item.kind, target, len(chunk), item.store, n, len(chunks)))
    return out


def order_items(items, upload_order="vendor"):
    def vendor_key(it):
        return (it.vendor.lower() == UNMAPPED, it.vendor.lower())

    kind_rank = {INVENTORY: 0, PRICE: 1}
    if upload_order == "kind":
        return sorted(items, key=lambda it: (kind_rank[it.kind], *vendor_key(it), it.part))
    return sorted(items, key=lambda it: (*vendor_key(it), kind_rank[it.kind], it.part))


def collect_files(source, work_dir, *, include_unmapped=False, only=None, max_rows=0, upload_order="vendor",
                  kinds=None):
    """Return (items to upload, [(file name, reason skipped)]). ``kinds``: inventory/price to keep (None = both)."""
    source = Path(source)
    work_dir = Path(work_dir)
    if not source.exists():
        raise FilesError(f"{source} not found.")
    if source.is_file():
        if source.suffix.lower() != ".zip":
            raise FilesError(f"{source.name} is not a ZIP. Use the 'Templates by Vendor' ZIP from the web app.")
        if work_dir.exists():
            shutil.rmtree(work_dir)
        files = _extract_zip(source, work_dir / "files")
    else:
        files = []
        for p in sorted(source.rglob("*.csv")):
            rel = p.relative_to(source).parts
            files.append((p, rel[0] if len(rel) > 1 else ""))
    if not files:
        raise FilesError(f"No CSV files in {source.name}.")

    wanted = {v.strip().lower() for v in (only or []) if v.strip()}
    items, skipped, stores = [], [], set()
    for path, folder in files:
        vendor, store = _vendor_and_store(path, folder)
        header, body = _read_csv(path)
        kind = kind_from_headers(header)
        if kind is None:
            skipped.append((path.name, "not a MyDeal Price or Inventory file (header row does not match)"))
            continue
        if vendor.lower() == UNMAPPED and not include_unmapped:
            skipped.append((path.name, "Unmapped SKUs are skipped (use --include-unmapped to upload them)"))
            continue
        if kinds and kind not in kinds:
            skipped.append((path.name, f"{kind} files not chosen for this run"))
            continue
        if wanted and vendor.lower() not in wanted:
            skipped.append((path.name, "vendor not in --only"))
            continue
        if not body:
            skipped.append((path.name, "no data rows"))
            continue
        if store:
            stores.add(store)
        item = UploadItem(vendor, kind, path, len(body), store)
        if max_rows and len(body) > max_rows:
            items.extend(_split(item, header, body, max_rows, work_dir / "parts"))
        else:
            items.append(item)
    if len(stores) > 1:
        raise FilesError(f"The files are for more than one store ({', '.join(sorted(stores))}). "
                         "Use one store's ZIP per run.")
    if wanted:
        found = {it.vendor.lower() for it in items}
        missing = sorted(wanted - found)
        if missing:
            raise FilesError(f"No files for: {', '.join(missing)}.")
    return order_items(items, upload_order), skipped


def newest_zip(folder):
    Path(folder).mkdir(parents=True, exist_ok=True)
    zips = sorted(Path(folder).glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        raise FilesError(f"No ZIP in {folder}. Put the 'Templates by Vendor' ZIP there or pass --zip.")
    return zips[0]
