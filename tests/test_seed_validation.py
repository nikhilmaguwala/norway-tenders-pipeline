from pathlib import Path

import pytest

from norway_tenders.matching.matcher import match_pack, parse_pack_size
from norway_tenders.models import MoleculeMatch, OutputRow
from norway_tenders.normalise.lifecycle import build_seed_pack_row, validate_seed_pack_rows
from norway_tenders.parsers.lis_excel import extract_workbook_buyer, parse_lis_prisskjema

SEEDS = Path(__file__).resolve().parents[1] / "data" / "seeds"
PRISSKJEMA = SEEDS / "LIS 2234 Vedlegg 03 Prisskjema.xlsx"
KRAVSPEC = SEEDS / "LIS 2234 Vedlegg 02 Kravspesifikasjon.xlsx"


def _resolve(path: Path) -> Path:
    if path.exists() and path.read_bytes()[:2] == b"PK":
        return path
    pytest.skip(f"Fixture not available: {path.name}")


def _sample_row(**overrides) -> OutputRow:
    match = MoleculeMatch(
        product_molecule="Lenalidomide",
        molecule_detected=True,
        molecule_variant="Lenalidomid",
        detection_method="name_in_document",
        atc_code="L04AX04",
    )
    path = _resolve(PRISSKJEMA)
    pack = parse_lis_prisskjema(path)[0]
    row = build_seed_pack_row(
        pack,
        match,
        tender_ref="LIS 2234",
        source_document=path.name,
        buyer=extract_workbook_buyer(_resolve(KRAVSPEC)),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_tender_ref_not_copied_into_notice_id() -> None:
    row = _sample_row()
    assert row.notice_id == ""
    assert row.tender_ref == "LIS 2234"
    assert row.notice_id != row.tender_ref.replace(" ", "")


def test_invented_notice_type_and_status_rejected() -> None:
    row = _sample_row(notice_type="contract notice", status="open")
    errors = validate_seed_pack_rows([row])
    assert any("noticeType" in e for e in errors)
    assert any("status" in e for e in errors)


def test_synthetic_file_url_rejected() -> None:
    row = _sample_row(source_url="file://local/LIS2234")
    errors = validate_seed_pack_rows([row])
    assert any("file://" in e for e in errors)


def test_numeric_pack_size_not_text() -> None:
    assert parse_pack_size("21 ENPAC") == 21
    assert isinstance(parse_pack_size("21 ENPAC"), int)
    row = _sample_row()
    assert isinstance(row.pack_size, int)
    assert row.pack_size == 21


def test_unsupported_notice_metadata_not_inserted_during_seed_parsing() -> None:
    path = _resolve(PRISSKJEMA)
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
            )
        )
    errors = validate_seed_pack_rows(rows)
    assert errors == []
    for row in rows:
        assert row.notice_id == ""
        assert row.title == ""
        assert row.publication_date == ""
        assert row.contract_start == ""
        assert row.procedure_type == ""
        assert row.source_url == ""
