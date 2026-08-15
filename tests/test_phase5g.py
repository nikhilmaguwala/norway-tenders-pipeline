from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from norway_tenders.normalise.export_semantics import (
    clean_pack_size_for_export,
    clean_supplier_for_export,
    map_notice_type,
    map_procedure_type,
    row_to_export_dict,
    serialize_csv_value,
)
from norway_tenders.models import OutputRow
from norway_tenders.settings import OUTPUT_COLUMNS, OUTPUT_CSV, PROCESSED_DIR
from norway_tenders.validation.phase5g import run_offline_build, run_phase5g


def test_spreadsheet_error_supplier_cleaned_to_blank() -> None:
    cleaned, reason = clean_supplier_for_export("#N/A")
    assert cleaned == ""
    assert reason == "spreadsheet_error_placeholder"


def test_grouping_label_rejected_as_supplier() -> None:
    cleaned, reason = clean_supplier_for_export("Grossister vgr 6")
    assert cleaned == ""
    assert reason == "supplier_grouping_label_not_legal_entity"


def test_no_supplier_inferred_from_product_name() -> None:
    row = OutputRow(product_name="Xeplion Depotinjeksjonsvæske", supplier="")
    cleaned, reason = clean_supplier_for_export(row.supplier)
    assert cleaned == ""
    assert reason == ""


def test_zero_pack_size_rejected() -> None:
    cleaned, warning = clean_pack_size_for_export(0)
    assert cleaned is None
    assert warning == "invalid_zero_pack_size"


def test_zero_sales_volume_preserved_in_export_dict() -> None:
    row = OutputRow(packs_sold_last_12m=0)
    export = row_to_export_dict(row)
    assert export["packsSoldLast12m"] == 0


def test_notice_type_mapping() -> None:
    assert map_notice_type("3")[0] == "Contract notice"
    assert map_notice_type("cn-standard")[0] == "Competition notice"
    assert map_notice_type("V")[0] == "Voluntary ex ante transparency notice"


def test_procedure_type_mapping() -> None:
    assert map_procedure_type("1")[0] == "Open procedure"
    assert map_procedure_type("open")[0] == "Open procedure"
    assert map_procedure_type("V", notice_id="682047-2022")[0] == "Award without prior publication"


def test_unknown_code_remains_blank() -> None:
    label, warning = map_notice_type("ZZ99")
    assert label == ""
    assert "unknown_notice_type" in warning


def test_integral_numeric_serialization() -> None:
    assert serialize_csv_value("packSize", 56.0) == 56
    assert serialize_csv_value("packsSoldLast12m", 179.0) == 179
    assert serialize_csv_value("estimatedValue", 320000000.0) == 320000000
    assert serialize_csv_value("maxPrice", 6573.91) == 6573.91


@pytest.fixture(scope="module")
def phase5g_output() -> Path:
    run_phase5g()
    return OUTPUT_CSV


def test_full_output_contains_no_spreadsheet_errors(phase5g_output) -> None:
    text = phase5g_output.read_text(encoding="utf-8")
    assert "#N/A" not in text
    assert "#VALUE!" not in text
    assert "#REF!" not in text
    assert "Grossister vgr 6" not in text


def test_offline_deterministic_build() -> None:
    first = run_offline_build()
    sha1 = first.output_sha256
    second = run_offline_build()
    assert second.output_sha256 == sha1
    assert hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest() == sha1


def test_output_stability_except_cleaned_fields(phase5g_output) -> None:
    rows = list(csv.DictReader(phase5g_output.open(encoding="utf-8")))
    assert len(rows) == 41
    assert list(rows[0].keys()) == OUTPUT_COLUMNS

    item_39496 = next(r for r in rows if r["itemNumber"] == "39496")
    assert item_39496["packSize"] == ""

    item_214209 = next(r for r in rows if r["itemNumber"] == "214209")
    assert item_214209["supplier"] == ""

    item_362150 = next(r for r in rows if r["itemNumber"] == "362150")
    assert item_362150["supplier"] == ""
    assert item_362150["packsSoldLast12m"] in {"0", 0}

    assert item_39496["noticeType"] == "Competition notice"
    assert item_39496["procedureType"] == "Open procedure"

    veat = next(r for r in rows if r["noticeId"] == "682047-2022")
    assert veat["noticeType"] == "Voluntary ex ante transparency notice"
    assert veat["procedureType"] == "Award without prior publication"

    audit_path = PROCESSED_DIR / "final_semantic_cleanup_audit.csv"
    assert audit_path.exists()
    evidence = list(csv.DictReader((PROCESSED_DIR / "pack_evidence.csv").open(encoding="utf-8")))
    ev_214209 = next(r for r in evidence if r["itemNumber"] == "214209")
    assert ev_214209["rawSupplier"] == "Grossister vgr 6"
    assert ev_214209["supplierExportDecision"] == "blanked"
    ev_39496 = next(r for r in evidence if r["itemNumber"] == "39496")
    assert ev_39496["rawPackDescription"] == "0"
    assert "invalid_zero_pack_size" in ev_39496["parserWarnings"]
