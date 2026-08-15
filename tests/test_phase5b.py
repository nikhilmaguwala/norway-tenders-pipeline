from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from norway_tenders.extraction.row_filter import evaluate_pack_for_target
from norway_tenders.models import PackRecord, Provenance
from norway_tenders.parsers.lis_excel import SheetLayout, parse_lis_prisskjema
from norway_tenders.settings import OUTPUT_COLUMNS, PROCESSED_DIR, SEEDS_DIR
from norway_tenders.validation.phase5b import (
    dedupe_preview_rows,
    preview_row_key,
    run_phase5b,
    validate_preview,
)
from norway_tenders.validation.seed_config import PALIPERIDONE_NOTICE_TOTAL_NOK

AXITINIB_PRISSKJEMA = (
    SEEDS_DIR / "Axitinib__LIS_2207_Oncology" / "LIS 2207 - Vedlegg 03 Prisskjema v 2.xlsx"
)
EVEROLIMUS_PRISSKJEMA = SEEDS_DIR / "Everolimus__2632a" / "2632a Bilag 2 Prisskjema.xlsx"
PALIPERIDONE_PRISSKJEMA = (
    SEEDS_DIR / "Paliperidone__LIS_2301d" / "2301d Vedlegg 03 Prisskjema legem versj1.xlsx"
)


@pytest.fixture(scope="module")
def phase5b_outputs() -> dict[str, Path]:
    result = run_phase5b()
    return {
        "preview": result.preview_path,
        "audit": result.audit_path,
        "quality": result.quality_path,
    }


def test_axitinib_only_l01ek01_rows_accepted() -> None:
    layout = SheetLayout(sheet_name="Prisskjema", header_row=3, data_start_row=4)
    packs = parse_lis_prisskjema(AXITINIB_PRISSKJEMA, layout=layout)
    accepted = [p for p in packs if evaluate_pack_for_target(p, "Axitinib").accepted]
    assert len(accepted) == 4
    assert all(p.atc_code == "L01EK01" for p in accepted)
    assert all("inlyta" in p.product_name.casefold() for p in accepted)


def test_inlyta_without_atc_rejected() -> None:
    pack = PackRecord(
        item_number="12345",
        product_name="Inlyta tab 1mg",
        provenance=Provenance(sheet="Prisskjema", row=1),
    )
    result = evaluate_pack_for_target(pack, "Axitinib")
    assert not result.accepted
    assert result.rejection_reason == "brand_only_insufficient"


def test_mycophenolic_acid_excluded_from_everolimus() -> None:
    layout = SheetLayout(sheet_name="Prisskjema", header_row=4, data_start_row=5)
    packs = parse_lis_prisskjema(EVEROLIMUS_PRISSKJEMA, layout=layout)
    myk = [p for p in packs if p.atc_code == "L04AA06"]
    assert myk
    assert all(not evaluate_pack_for_target(p, "Everolimus").accepted for p in myk)


def test_unrelated_lis_2301d_rows_excluded_from_paliperidone() -> None:
    layout = SheetLayout(sheet_name="Prisskjema", header_row=3, data_start_row=4)
    packs = parse_lis_prisskjema(PALIPERIDONE_PRISSKJEMA, layout=layout)
    accepted = [p for p in packs if evaluate_pack_for_target(p, "Paliperidone").accepted]
    assert len(accepted) == 2
    assert all(p.atc_code == "N05AX13" for p in accepted)


def test_atc_only_axitinib_semantics(phase5b_outputs) -> None:
    rows = list(csv.DictReader(phase5b_outputs["preview"].open(encoding="utf-8")))
    ax = [r for r in rows if r["productMolecule"] == "Axitinib"]
    assert ax
    assert all(r["detectionMethod"] == "atc_in_document" for r in ax)
    assert all(r["moleculeDetected"] == "False" for r in ax)
    assert all(r["moleculeVariant"] == "" for r in ax)
    assert all("Inlyta" in r["productName"] for r in ax)


def test_lifecycle_duplicates_not_multiplying_packs() -> None:
    rows_a = list(csv.DictReader((PROCESSED_DIR / "output_preview.csv").open(encoding="utf-8")))
    rows_b = list(csv.DictReader((PROCESSED_DIR / "output_preview.csv").open(encoding="utf-8")))
    assert rows_a == rows_b
    assert len({r["noticeId"] for r in rows_a if r["productMolecule"] == "Lenalidomide"}) == 1


def test_umbrella_values_not_allocated(phase5b_outputs) -> None:
    rows = list(csv.DictReader(phase5b_outputs["preview"].open(encoding="utf-8")))
    for row in rows:
        est = row.get("estimatedValue") or ""
        award = row.get("awardedValue") or ""
        assert est == ""
        assert award == ""
        if row["productMolecule"] == "Axitinib":
            assert est != "3200000000"
        if row["productMolecule"] == "Paliperidone":
            assert award != str(PALIPERIDONE_NOTICE_TOTAL_NOK)


def test_exact_28_column_preview_schema(phase5b_outputs) -> None:
    rows = list(csv.DictReader(phase5b_outputs["preview"].open(encoding="utf-8")))
    assert rows
    assert list(rows[0].keys()) == OUTPUT_COLUMNS


def test_unique_preview_row_keys(phase5b_outputs) -> None:
    rows = list(csv.DictReader(phase5b_outputs["preview"].open(encoding="utf-8")))
    keys = {
        preview_row_key_from_csv(row)
        for row in rows
    }
    assert len(keys) == len(rows)


def preview_row_key_from_csv(row: dict[str, str]) -> str:
    procedure = (row["tenderRef"] or row["noticeId"]).replace(" ", "").upper()
    if row["itemNumber"]:
        return f"{procedure}|{row['productMolecule']}|{row['itemNumber']}"
    return f"{procedure}|{row['productMolecule']}|{row['sourceDocument']}|0"


def test_at_least_one_row_per_molecule(phase5b_outputs) -> None:
    quality = json.loads(phase5b_outputs["quality"].read_text(encoding="utf-8"))
    for molecule in ["Axitinib", "Everolimus", "Lenalidomide", "Anagrelide", "Paliperidone"]:
        assert quality["accepted_pack_rows_by_molecule"].get(molecule, 0) >= 1


def test_blank_vs_zero_volume_preservation(phase5b_outputs) -> None:
    rows = list(csv.DictReader(phase5b_outputs["preview"].open(encoding="utf-8")))
    explicit_zero = [r for r in rows if r["itemNumber"] == "362150"]
    assert explicit_zero
    assert explicit_zero[0]["packsSoldLast12m"] in {"0", "0.0"}
    blank_max = [r for r in rows if r["productMolecule"] == "Axitinib" and r["itemNumber"] == "103854"]
    assert blank_max[0]["maxPrice"] == ""


def test_lenalidomide_volume_unchanged(phase5b_outputs) -> None:
    rows = list(csv.DictReader(phase5b_outputs["preview"].open(encoding="utf-8")))
    lena = [r for r in rows if r["productMolecule"] == "Lenalidomide"]
    total = sum(float(r["packsSoldLast12m"]) for r in lena if r["packsSoldLast12m"])
    assert len(lena) == 7
    assert total == 7188


def test_phase5b_deliverables_exist(phase5b_outputs) -> None:
    for key in ("preview", "audit", "quality"):
        assert phase5b_outputs[key].exists()
    assert (PROCESSED_DIR / "pack_evidence.csv").exists()
    assert (PROCESSED_DIR / "lifecycle_linkage.csv").exists()
    quality = json.loads(phase5b_outputs["quality"].read_text(encoding="utf-8"))
    assert quality["validation"]["passed"] is True
