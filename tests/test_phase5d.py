from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from norway_tenders.extraction.layouts import layout_for_local_file
from norway_tenders.extraction.row_filter import evaluate_pack_for_target
from norway_tenders.parsers.lis_excel import parse_lis_prisskjema
from norway_tenders.settings import OUTPUT_COLUMNS, PROCESSED_DIR, SEEDS_DIR
from norway_tenders.validation.phase5a import discover_seed_files
from norway_tenders.validation.phase5b import dedupe_preview_rows, preview_row_key, run_phase5b
from norway_tenders.validation.seed_config import HUMAN_READABLE_SEED_FOLDERS

PRISSKJEMA_2601C = SEEDS_DIR / "Paliperidone__2601c" / "2601c_Bilag_2_Prisskjema.xlsx"
PRISSKJEMA_2301D = (
    SEEDS_DIR / "Paliperidone__LIS_2301d" / "2301d Vedlegg 03 Prisskjema legem versj1.xlsx"
)

BASELINE_MOLECULE_COUNTS = {
    "Axitinib": 4,
    "Everolimus": 6,
    "Lenalidomide": 7,
    "Anagrelide": 6,
    "Paliperidone": 2,
}
BASELINE_TOTAL = 25


@pytest.fixture(scope="module")
def phase5d_outputs() -> dict[str, Path]:
    result = run_phase5b()
    return {
        "preview": result.preview_path,
        "audit": result.audit_path,
        "quality": result.quality_path,
    }


def test_recursive_discovery_of_paliperidone_2601c() -> None:
    files = discover_seed_files()
    folders = {p.relative_to(SEEDS_DIR).parts[0] for p in files}
    assert "Paliperidone__2601c" in folders
    assert "Paliperidone__2601c" in HUMAN_READABLE_SEED_FOLDERS
    pali_files = [p for p in files if p.parts[-2] == "Paliperidone__2601c"]
    assert len(pali_files) == 5
    assert any("Prisskjema" in p.name for p in pali_files)


def test_strict_n05ax13_name_filtering_2601c() -> None:
    layout = layout_for_local_file("Paliperidone__2601c/2601c_Bilag_2_Prisskjema.xlsx")
    packs = parse_lis_prisskjema(PRISSKJEMA_2601C, layout=layout)
    accepted = [p for p in packs if evaluate_pack_for_target(p, "Paliperidone").accepted]
    rejected = [p for p in packs if not evaluate_pack_for_target(p, "Paliperidone").accepted]
    assert len(accepted) == 16
    assert rejected == []
    assert all(p.atc_code == "N05AX13" for p in accepted)
    assert all(
        "paliperidon" in (p.provenance.raw_values.get("active_substance", "") or "").casefold()
        or "paliperidon" in p.product_name.casefold()
        for p in accepted
    )


def test_2601c_and_lis_2301d_distinct_procedures(phase5d_outputs) -> None:
    rows = list(csv.DictReader(phase5d_outputs["preview"].open(encoding="utf-8")))
    pali = [r for r in rows if r["productMolecule"] == "Paliperidone"]
    notices = {r["noticeId"] for r in pali}
    tender_refs = {r["tenderRef"] for r in pali}
    assert "434619-2026" in notices
    assert "682047-2022" in notices
    assert "2601c" in tender_refs
    assert "2022/227" in tender_refs


def test_repeated_product_across_procedures_retained() -> None:
    from norway_tenders.models import OutputRow

    row_2301d = OutputRow(
        notice_id="682047-2022",
        tender_ref="2022/227",
        product_molecule="Paliperidone",
        item_number="214209",
        source_document="2301d Vedlegg 03 Prisskjema legem versj1.xlsx",
    )
    row_2601c = OutputRow(
        notice_id="434619-2026",
        tender_ref="2601c",
        product_molecule="Paliperidone",
        item_number="214209",
        source_document="2601c_Bilag_2_Prisskjema.xlsx",
    )
    deduped, _ = dedupe_preview_rows([row_2301d, row_2601c], [425, 9])
    assert len(deduped) == 2
    assert preview_row_key(row_2301d, 425) != preview_row_key(row_2601c, 9)


def test_duplicate_item_within_2601c_removed() -> None:
    layout = layout_for_local_file("Paliperidone__2601c/2601c_Bilag_2_Prisskjema.xlsx")
    packs = parse_lis_prisskjema(PRISSKJEMA_2601C, layout=layout)
    from norway_tenders.models import NoticeRecord, Provenance
    from norway_tenders.normalise.lifecycle import build_pack_row

    notice = NoticeRecord(
        notice_id="434619-2026",
        tender_ref="2601c",
        title="2601c paliperidone",
        country="NO",
        source_url="https://ted.europa.eu/en/notice/-/detail/434619-2026",
        provenance=Provenance(),
    )
    rows = []
    source_rows = []
    for pack in packs:
        result = evaluate_pack_for_target(pack, "Paliperidone")
        if not result.accepted:
            continue
        row = build_pack_row(
            notice, result.match, pack,
            source_document=PRISSKJEMA_2601C.name,
            source_url=notice.source_url,
        )
        rows.append(row)
        source_rows.append(pack.provenance.row)
    # Inject artificial duplicate within same procedure
    rows.append(rows[0])
    source_rows.append(source_rows[0])
    deduped, _ = dedupe_preview_rows(rows, source_rows)
    assert len(deduped) == len(rows) - 1


def test_existing_25_row_preview_stable_before_append(phase5d_outputs) -> None:
    rows = list(csv.DictReader(phase5d_outputs["preview"].open(encoding="utf-8")))
    baseline = [r for r in rows if r["noticeId"] != "434619-2026"]
    assert len(baseline) == BASELINE_TOTAL
    counts = Counter(r["productMolecule"] for r in baseline)
    assert dict(counts) == BASELINE_MOLECULE_COUNTS


def test_2601c_rows_use_canonical_notice(phase5d_outputs) -> None:
    rows = list(csv.DictReader(phase5d_outputs["preview"].open(encoding="utf-8")))
    new_rows = [r for r in rows if r["noticeId"] == "434619-2026"]
    assert len(new_rows) == 16
    assert all(r["tenderRef"] == "2601c" for r in new_rows)
    assert all(r["sourceUrl"] == "https://ted.europa.eu/en/notice/-/detail/434619-2026" for r in new_rows)
    assert all(r["sourceDocument"] == "2601c_Bilag_2_Prisskjema.xlsx" for r in new_rows)
    assert all(r["detectionMethod"] == "name_in_document" for r in new_rows)
    assert all(r["moleculeDetected"] == "True" for r in new_rows)


def test_preview_at_least_40_rows(phase5d_outputs) -> None:
    rows = list(csv.DictReader(phase5d_outputs["preview"].open(encoding="utf-8")))
    assert len(rows) >= 40
    quality = json.loads(phase5d_outputs["quality"].read_text(encoding="utf-8"))
    assert quality["within_40_120_rows"] is True


def test_phase5d_schema_and_validation(phase5d_outputs) -> None:
    rows = list(csv.DictReader(phase5d_outputs["preview"].open(encoding="utf-8")))
    assert list(rows[0].keys()) == OUTPUT_COLUMNS
    quality = json.loads(phase5d_outputs["quality"].read_text(encoding="utf-8"))
    assert quality["validation"]["passed"] is True
    keys = {preview_row_key_from_csv(r) for r in rows}
    assert len(keys) == len(rows)


def preview_row_key_from_csv(row: dict[str, str]) -> str:
    procedure = (row["tenderRef"] or row["noticeId"]).replace(" ", "").upper()
    if row["itemNumber"]:
        return f"{procedure}|{row['productMolecule']}|{row['itemNumber']}"
    return f"{procedure}|{row['productMolecule']}|{row['sourceDocument']}|0"
