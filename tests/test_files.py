import zipfile

import pytest

from core.files import INVENTORY, PRICE, FilesError, collect_files, kind_from_headers, newest_zip
from core.files import INVENTORY_HEADERS, PRICE_HEADERS

from .helpers import vendor_zip


@pytest.fixture
def zip_path(tmp_path):
    return vendor_zip(tmp_path / "by_vendor.zip", {
        "Vevor": (["V1", "V2"], ["V1", "V2"]),
        "Amazon AU": (["A1"], ["A1"]),
        "Unmapped": (["U1"], ["U1"]),
    })


def labels(items):
    return [(it.vendor, it.kind) for it in items]


def test_kind_from_headers():
    assert kind_from_headers(PRICE_HEADERS) == PRICE
    assert kind_from_headers(["﻿DealID", *INVENTORY_HEADERS[1:], ""]) == INVENTORY
    assert kind_from_headers(["SKU", "Price"]) is None


def test_vendor_order_inventory_then_price_and_unmapped_skipped(zip_path, tmp_path):
    items, skipped = collect_files(zip_path, tmp_path / "work")
    assert labels(items) == [("Amazon AU", INVENTORY), ("Amazon AU", PRICE), ("Vevor", INVENTORY), ("Vevor", PRICE)]
    assert {it.rows for it in items if it.vendor == "Vevor"} == {2}
    assert items[0].store == "BW-P&P"
    assert len(skipped) == 2 and all("Unmapped" in reason for _, reason in skipped)


def test_kind_order(zip_path, tmp_path):
    items, _ = collect_files(zip_path, tmp_path / "work", upload_order="kind")
    assert labels(items) == [("Amazon AU", INVENTORY), ("Vevor", INVENTORY), ("Amazon AU", PRICE), ("Vevor", PRICE)]


def test_include_unmapped_goes_last(zip_path, tmp_path):
    items, _ = collect_files(zip_path, tmp_path / "work", include_unmapped=True)
    assert labels(items)[-2:] == [("Unmapped", INVENTORY), ("Unmapped", PRICE)]


def test_only_filters_vendors_and_reports_missing(zip_path, tmp_path):
    items, _ = collect_files(zip_path, tmp_path / "work", only=["vevor"])
    assert {it.vendor for it in items} == {"Vevor"}
    with pytest.raises(FilesError, match="No files for: costco"):
        collect_files(zip_path, tmp_path / "work2", only=["Costco"])


def test_split_big_files(tmp_path):
    z = vendor_zip(tmp_path / "z.zip", {"Vevor": ([f"S{i}" for i in range(5)], None)})
    items, _ = collect_files(z, tmp_path / "work", max_rows=2)
    assert [(it.part, it.parts, it.rows) for it in items] == [(1, 3, 2), (2, 3, 2), (3, 3, 1)]
    assert items[0].label == "Vevor Price (part 1 of 3)"
    lines = items[2].path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(PRICE_HEADERS) and lines[1].split(",")[3] == "S4"


def test_empty_and_wrong_files_are_skipped(tmp_path):
    z = tmp_path / "z.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Vevor/Mydeal - S - Vevor - Price Template.csv", ",".join(PRICE_HEADERS) + "\n")
        zf.writestr("Vevor/notes.csv", "a,b\n1,2\n")
        zf.writestr("Vevor/readme.txt", "hi")
    items, skipped = collect_files(z, tmp_path / "work")
    assert items == []
    assert sorted(reason for _, reason in skipped) == [
        "no data rows", "not a MyDeal Price or Inventory file (header row does not match)"]


def test_two_stores_rejected(tmp_path):
    z = tmp_path / "z.zip"
    vendor_zip(z, {"Vevor": (["V1"], None)}, store="TFS")
    with zipfile.ZipFile(z, "a") as zf:
        zf.writestr("Amazon/Mydeal - BW-P&P - Amazon - Price Template.csv",
                    ",".join(PRICE_HEADERS) + "\nD,V,E,A1,,T,1,2\n")
    with pytest.raises(FilesError, match="more than one store"):
        collect_files(z, tmp_path / "work")


def test_zip_paths_cannot_escape_work_dir(tmp_path):
    z = tmp_path / "z.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../evil/Mydeal - S - X - Price Template.csv", ",".join(PRICE_HEADERS) + "\nD,V,E,A,,T,1,2\n")
    items, _ = collect_files(z, tmp_path / "work")
    assert (tmp_path / "work").resolve() in items[0].path.resolve().parents
    assert not (tmp_path / "evil").exists()


def test_folder_source(tmp_path):
    z = vendor_zip(tmp_path / "z.zip", {"Vevor": (["V1"], ["V1"])})
    folder = tmp_path / "folder"
    with zipfile.ZipFile(z) as zf:
        zf.extractall(folder)
    items, _ = collect_files(folder, tmp_path / "work")
    assert labels(items) == [("Vevor", INVENTORY), ("Vevor", PRICE)]


def test_not_a_zip_and_newest_zip(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a")
    with pytest.raises(FilesError, match="not a ZIP"):
        collect_files(f, tmp_path / "work")
    with pytest.raises(FilesError, match="No ZIP"):
        newest_zip(tmp_path / "input")
    vendor_zip(tmp_path / "input" / "a.zip", {"V": (["1"], None)})
    assert newest_zip(tmp_path / "input").name == "a.zip"
