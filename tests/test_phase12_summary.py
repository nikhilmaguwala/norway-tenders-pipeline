from __future__ import annotations

import json
from pathlib import Path

import pytest

from norway_tenders.matching.matcher import match_pack
from norway_tenders.normalise.lifecycle import build_seed_pack_row, validate_seed_pack_rows
from norway_tenders.parsers.lis_excel import (
    extract_workbook_buyer,
    parse_lis_prisskjema,
)

SEEDS = Path(__file__).resolve().parents[1] / "data" / "seeds"
FIXTURES = Path(__file__).parent / "fixtures"
PRISSKJEMA = SEEDS / "LIS 2234 Vedlegg 03 Prisskjema.xlsx"
KRAVSPEC = SEEDS / "LIS 2234 Vedlegg 02 Kravspesifikasjon.xlsx"


def _resolve(path: Path) -> Path:
    if path.exists() and path.read_bytes()[:2] == b"PK":
        return path
    alt = FIXTURES / path.name
    if alt.exists() and alt.read_bytes()[:2] == b"PK":
        return alt
    pytest.skip(f"Golden fixture not available: {path.name}")


def _build_lis2234_seed_rows():
    path = _resolve(PRISSKJEMA)
    kravspec = _resolve(KRAVSPEC)
    buyer = extract_workbook_buyer(kravspec) or extract_workbook_buyer(path)
    packs = parse_lis_prisskjema(path)
    rows = []
    for pack in packs:
        match = match_pack(pack, context="document")
        assert match is not None
        rows.append(
            build_seed_pack_row(
                pack,
                match,
                tender_ref="LIS 2234",
                source_document=path.name,
                buyer=buyer,
            )
        )
    return rows


@pytest.mark.golden
def test_phase12_lis2234_pack_output_semantics() -> None:
    rows = _build_lis2234_seed_rows()
    assert len(rows) == 7
    for row in rows:
        assert row.notice_id == ""
        assert row.product_molecule == "Lenalidomide"
        assert row.molecule_detected is True
        assert row.molecule_variant == "Lenalidomid"
        assert row.detection_method == "name_in_document"
        assert row.atc_code == "L04AX04"
        assert row.tender_ref == "LIS 2234"
        assert row.title == ""
        assert row.notice_type == ""
        assert row.status == ""
        assert row.source_url == ""


def test_phase12_summary_report(capsys: pytest.CaptureFixture[str]) -> None:
    rows = _build_lis2234_seed_rows()
    volume = sum(r.packs_sold_last_12m or 0 for r in rows)
    max_price_nonempty = sum(1 for r in rows if r.max_price is not None)

    summary = {
        "pack_row_count": len(rows),
        "volume_total": volume,
        "unique_atcCode": sorted({r.atc_code for r in rows}),
        "unique_productMolecule": sorted({r.product_molecule for r in rows}),
        "unique_moleculeDetected": sorted({r.molecule_detected for r in rows}),
        "unique_moleculeVariant": sorted({r.molecule_variant for r in rows}),
        "unique_detectionMethod": sorted({r.detection_method for r in rows}),
        "nonempty_maxPrice_count": max_price_nonempty,
        "nonempty_offered_price_derived_count": 0,
        "example_output_row": rows[0].to_csv_dict(),
    }
    print("\n=== Phase 1/2 summary ===")
    print(json.dumps(summary, indent=2, default=str))
    assert len(rows) == 7
    assert volume == 7188
    assert all(isinstance(r.pack_size, int) for r in rows)
    assert validate_seed_pack_rows(rows) == []
