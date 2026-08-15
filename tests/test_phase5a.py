from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from norway_tenders.parsers.lis_excel import parse_lis_prisskjema
from norway_tenders.settings import DISCOVERY_DIR, SEEDS_DIR, SOURCES_SEED
from norway_tenders.validation.file_probe import probe_file
from norway_tenders.validation.phase5a import (
    discover_seed_files,
    run_phase5a,
    validate_local_files,
)
from norway_tenders.validation.seed_config import HUMAN_READABLE_SEED_FOLDERS

EXPECTED_SHA256 = {
    "Anagrelide__2507gj-1/2507gj-1 Bilag 2 Prisskjema.xlsx": "dc2beaddcdd15687aa3fa60e282af705e3cd7671e3a4bd28930031a23b4ad6aa",
    "Lenalidomide__LIS_2234/LIS 2234 Vedlegg 03 Prisskjema.xlsx": "8ae5601e07748f775695a675aa696d3d4e6e256d1f55ed99e0e5ce4e2d09d789",
}

MOLECULE_TERMS = {
    "Axitinib": ["axitinib", "L01EK01", "L01XE17", "inlyta"],
    "Everolimus": ["everolimus", "L01EG02", "L04AH02"],
    "Lenalidomide": ["lenalidomide", "lenalidomid", "L04AX04"],
    "Anagrelide": ["anagrelide", "anagrelid", "L01XX35", "B01AC14"],
    "Paliperidone": ["paliperidone", "paliperidon", "N05AX13"],
}


@pytest.fixture(scope="module")
def phase5a_outputs() -> dict[str, Path]:
    result = run_phase5a()
    return {
        "validation": result.validation_path,
        "inventory": result.inventory_path,
        "matches": result.matches_path,
        "parser": result.parser_path,
        "summary": result.summary_path,
    }


def test_recursive_discovery_of_seed_folders() -> None:
    files = discover_seed_files()
    folders = {p.relative_to(SEEDS_DIR).parts[0] for p in files}
    assert folders == set(HUMAN_READABLE_SEED_FOLDERS)
    assert len(files) == 21
    assert "Paliperidone__2601c" in folders


def test_file_signature_validation() -> None:
    validations = validate_local_files()
    by_file = {v.local_file: v for v in validations}
    xlsx = by_file["Lenalidomide__LIS_2234/LIS 2234 Vedlegg 03 Prisskjema.xlsx"]
    pdf = by_file["Paliperidone__LIS_2301d/2301d Konkurransebestemmelser.pdf"]
    assert xlsx.detected_type == "xlsx"
    assert xlsx.is_valid is True
    assert pdf.detected_type == "pdf"
    assert pdf.is_valid is True
    assert pdf.has_embedded_text is True


@pytest.mark.parametrize("molecule,terms", MOLECULE_TERMS.items())
def test_molecule_variants_found_in_inventory(phase5a_outputs, molecule: str, terms: list[str]) -> None:
    text = phase5a_outputs["inventory"].read_text(encoding="utf-8").casefold()
    summary = json.loads(phase5a_outputs["summary"].read_text(encoding="utf-8"))
    mol_summary = summary["molecule_confirmation"][molecule]
    if molecule == "Axitinib":
        ax = mol_summary["axitinib_confirmation"]
        assert ax["by_L01EK01"] or ax["by_axitinib_name"] or ax["by_inlyta_only"]
        return
    assert mol_summary["explicit_name_confirmed"] or mol_summary["atc_confirmed"], (
        f"No document confirmation for {molecule}"
    )
    assert any(term.casefold() in text for term in terms)


def test_atc_codes_in_matches(phase5a_outputs) -> None:
    rows = list(csv.DictReader(phase5a_outputs["matches"].open(encoding="utf-8")))
    atcs = {r["atcCode"] for r in rows if r["atcCode"]}
    assert "L04AX04" in atcs
    assert "L01EK01" in atcs
    assert "N05AX13" in atcs


def test_folder_name_not_used_as_molecule_evidence(phase5a_outputs) -> None:
    rows = list(csv.DictReader(phase5a_outputs["matches"].open(encoding="utf-8")))
    excel_hits = [r for r in rows if r["matchType"] in {"name", "atc", "brand_discovery"} and r["rowOrSection"].isdigit()]
    assert excel_hits, "Expected spreadsheet cell matches"
    for row in excel_hits:
        assert int(row["rowOrSection"]) > 0
        assert row["exactMatchedValue"]
        assert "__" not in row["exactMatchedValue"]


def test_brand_only_not_accepted_match(phase5a_outputs) -> None:
    rows = list(csv.DictReader(phase5a_outputs["matches"].open(encoding="utf-8")))
    brand_rows = [r for r in rows if r["evidenceLevel"] == "brand_only"]
    assert brand_rows, "Expected brand-only rows for discovery audit"
    assert all(r["warning"] == "brand_only_not_accepted" for r in brand_rows)
    summary = json.loads(phase5a_outputs["summary"].read_text(encoding="utf-8"))
    ax = summary["molecule_confirmation"]["Axitinib"]["axitinib_confirmation"]
    assert ax["by_L01EK01"] or not ax["by_inlyta_only"]


def test_offered_gip_never_becomes_max_price() -> None:
    path = SEEDS_DIR / "Lenalidomide__LIS_2234" / "LIS 2234 Vedlegg 03 Prisskjema.xlsx"
    packs = parse_lis_prisskjema(path)
    assert all(p.offered_gip is None for p in packs)
    assert all(p.max_price is None or p.max_price >= 0 for p in packs)


def test_paliperidone_notice_value_not_assigned_to_molecule(phase5a_outputs) -> None:
    summary = json.loads(phase5a_outputs["summary"].read_text(encoding="utf-8"))
    assert summary["paliperidone_safety_context"]["do_not_allocate_notice_total_to_molecule"] is True
    rows = list(csv.DictReader(phase5a_outputs["inventory"].open(encoding="utf-8")))
    pali_rows = [r for r in rows if "Paliperidone" in r.get("targetMolecule", "")]
    assert not any(
        "14671946" in (r.get("exactMatchedValue", "") or "").replace(" ", "").replace(",", "").replace(".", "")
        and r.get("field") == "molecule_match"
        for r in pali_rows
    )


def test_unknown_direct_document_urls_stay_blank(phase5a_outputs) -> None:
    rows = list(csv.DictReader(SOURCES_SEED.open(encoding="utf-8")))
    local_rows = [r for r in rows if r.get("localFile")]
    assert local_rows, "Expected local source rows"
    for row in local_rows:
        assert not (row.get("url") or "").startswith("file://")
        if row["accessStatus"] in {"downloaded_manually", "supplied_seed"} and row["source"] == "local_seed":
            assert row["url"] == ""


def test_lenalidomide_golden_seven_rows_volume_7188() -> None:
    path = SEEDS_DIR / "Lenalidomide__LIS_2234" / "LIS 2234 Vedlegg 03 Prisskjema.xlsx"
    packs = parse_lis_prisskjema(path)
    lena = [p for p in packs if p.atc_code == "L04AX04"]
    assert len(lena) == 7
    assert sum(p.packs_sold_last_12m or 0 for p in lena) == 7188


def test_source_sha256_values_unchanged() -> None:
    for rel, expected in EXPECTED_SHA256.items():
        path = SEEDS_DIR / rel
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    validations = validate_local_files()
    by_file = {v.local_file: v.sha256 for v in validations}
    for rel, expected in EXPECTED_SHA256.items():
        assert by_file[rel] == expected


def test_phase5a_deliverables_exist(phase5a_outputs) -> None:
    for key in ("validation", "inventory", "matches", "parser", "summary"):
        assert phase5a_outputs[key].exists()
    assert DISCOVERY_DIR.joinpath("phase5a_summary.json").exists()
