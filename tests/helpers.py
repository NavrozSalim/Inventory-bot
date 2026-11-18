import zipfile

from core.files import INVENTORY_HEADERS, PRICE_HEADERS

STORE = "BW-P&P"


def csv_text(headers, rows):
    return "\n".join([",".join(headers)] + [",".join(r) for r in rows]) + "\n"


def price_rows(*skus):
    return [[f"D{i}", f"V{i}", f"E{i}", s, "", f"Title {i}", "10.00", "20.00"] for i, s in enumerate(skus, 1)]


def inv_rows(*skus):
    return [[f"D{i}", f"V{i}", f"E{i}", s, "", f"Title {i}", "5", "FALSE", "Approved"] for i, s in enumerate(skus, 1)]


def vendor_zip(path, vendors, store=STORE):
    """A ZIP shaped like the web app's 'Templates by Vendor' download. vendors: {name: (price_skus, inv_skus)}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for vendor, (p_skus, i_skus) in vendors.items():
            if p_skus is not None:
                zf.writestr(f"{vendor}/Mydeal - {store} - {vendor} - Price Template.csv",
                            csv_text(PRICE_HEADERS, price_rows(*p_skus)))
            if i_skus is not None:
                zf.writestr(f"{vendor}/Mydeal - {store} - {vendor} - Inventory Template.csv",
                            csv_text(INVENTORY_HEADERS, inv_rows(*i_skus)))
    return path
