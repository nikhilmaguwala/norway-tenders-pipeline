from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from norway_tenders.enrichment.dmp_prices import discover_dmp_workbook, sha256_file
from norway_tenders.settings import OUTPUT_COLUMNS, OUTPUT_CSV, PROCESSED_DIR, SEEDS_DIR
from norway_tenders.validation.phase5f import (
    DMP_DIR_NAME,
    DMP_EFFECTIVE_DATE,
    DMP_EXPECTED_SHA256,
    DMP_WORKBOOK_REL,
    NOTICE_LEVEL_AGGREGATION_RULE,
    apply_metadata,
    build_award_metadata_audit,
    build_notice_value_audit,
    deduplicate_notice_metrics,
    run_offline_build,
    run_phase5f,
    sum_deduplicated_notice_field,
    validate_final_output,
)
from norway_tenders.validation.phase5e import OUTPUT_FINAL_CANDIDATE_CSV, _csv_row_to_output


@pytest.fixture(scope="module")
def phase5f_outputs() -> dict[str, Path]:
    result = run_phase5f(offline=True)
    return {
        "output": result.output_path,
        "value_audit": result.notice_value_audit_path,
        "award_audit": result.award_audit_path,
        "metadata_audit": result.notice_metadata_audit_path,
        "quality": result.quality_path,
    }


def test_dmp_directory_and_effective_date_correction() -> None:
    dmp_dir = SEEDS_DIR / DMP_DIR_NAME
    assert dmp_dir.is_dir()
    workbook = dmp_dir / "legemiddelpriser-2026-08-03.xlsx"
    assert workbook.exists()
    assert sha256_file(workbook) == DMP_EXPECTED_SHA256
    index_path = discover_dmp_workbook(SEEDS_DIR)
    assert DMP_DIR_NAME in str(index_path.parent)
    sources = list(csv.DictReader((SEEDS_DIR / "sources.csv").open(encoding="utf-8")))
    dmp = [r for r in sources if r.get("sourceType") == "official_maximum_price_reference"][0]
    assert dmp["localFile"] == DMP_WORKBOOK_REL
    assert dmp["effectiveDate"] == DMP_EFFECTIVE_DATE
    assert dmp["sha256"] == DMP_EXPECTED_SHA256


def test_dedicated_formal_notice_estimate_accepted(phase5f_outputs) -> None:
    audit = list(csv.DictReader(phase5f_outputs["value_audit"].open(encoding="utf-8")))
    accepted = [r for r in audit if r["mappingDecision"] == "accepted"]
    assert any(r["noticeId"] == "244859-2024" for r in accepted)
    assert any(r["noticeId"] == "300984-2021" for r in accepted)
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    ana = [r for r in rows if r["noticeId"] == "244859-2024"]
    assert all(r["estimatedValue"] == "10000000.0" for r in ana)


def test_umbrella_estimate_rejected(phase5f_outputs) -> None:
    audit = list(csv.DictReader(phase5f_outputs["value_audit"].open(encoding="utf-8")))
    ax = [r for r in audit if r["noticeId"] == "196990-2022" and r["valueType"] == "umbrella_multi_molecule_value"]
    assert ax
    assert all(r["mappingDecision"] == "rejected" for r in ax)
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    assert all(r["estimatedValue"] == "" for r in rows if r["noticeId"] == "196990-2022")


def test_historical_turnover_not_mapped_to_estimated_value(phase5f_outputs) -> None:
    audit = list(csv.DictReader(phase5f_outputs["value_audit"].open(encoding="utf-8")))
    turnover = [r for r in audit if r["valueType"] == "historical_max_aip_turnover"]
    assert turnover
    assert all(r["mappedField"] == "" for r in turnover)
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    lena = [r for r in rows if r["noticeId"] == "300984-2021"]
    assert len(lena) == 7
    assert all(r["estimatedValue"] == "320000000.0" for r in lena)


def test_multi_molecule_award_value_rejected(phase5f_outputs) -> None:
    audit = list(csv.DictReader(phase5f_outputs["value_audit"].open(encoding="utf-8")))
    pali = [r for r in audit if r["noticeId"] == "682047-2022"]
    assert pali
    assert all(r["mappingDecision"] == "rejected" for r in pali)
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    assert all(r["awardedValue"] == "" for r in rows if r["noticeId"] == "682047-2022")


def test_prospective_veat_supplier_not_treated_as_concluded_winner(phase5f_outputs) -> None:
    audit = list(csv.DictReader(phase5f_outputs["award_audit"].open(encoding="utf-8")))
    veat = [r for r in audit if r["awardNoticeId"] == "682047-2022"][0]
    assert veat["awardedSupplierRaw"] == "Nordic Pill AB"
    assert veat["mappedSupplier"] == ""
    assert veat["mappingDecision"] == "rejected"


def test_award_lifecycle_enrichment_without_pack_duplication(phase5f_outputs) -> None:
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    assert len(rows) == 41
    assert len([r for r in rows if r["noticeId"] == "300984-2021"]) == 7


def test_consistent_notice_level_values_across_pack_rows(phase5f_outputs) -> None:
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    by_notice: dict[str, set[str]] = {}
    for row in rows:
        by_notice.setdefault(row["noticeId"], set()).add(row["estimatedValue"])
    for notice_id, values in by_notice.items():
        assert len(values) == 1, notice_id


def test_notice_level_aggregation_deduplicates_by_notice_id(phase5f_outputs) -> None:
    rows = [_csv_row_to_output(r) for r in csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8"))]
    raw_sum = sum(float(r.estimated_value) for r in rows if r.estimated_value is not None)
    dedup_sum = sum_deduplicated_notice_field(rows, "estimatedValue")
    assert dedup_sum < raw_sum
    assert dedup_sum == 330000000.0
    assert NOTICE_LEVEL_AGGREGATION_RULE


def test_offline_deterministic_build() -> None:
    from norway_tenders.validation.phase5g import run_offline_build

    first = run_offline_build()
    sha1 = first.output_sha256
    second = run_offline_build()
    assert second.output_sha256 == sha1
    assert hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest() == sha1


def test_final_41_row_schema_output_stability(phase5f_outputs) -> None:
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    assert len(rows) == 41
    assert list(rows[0].keys()) == OUTPUT_COLUMNS
    parsed = [_csv_row_to_output(r) for r in rows]
    validation = validate_final_output(parsed)
    assert validation["passed"] is True
    quality = json.loads(phase5f_outputs["quality"].read_text(encoding="utf-8"))
    assert quality["row_count"] == 41
    assert quality["dmp_directory_correction"]["sha256_unchanged"] is True


def test_2601c_open_status_only_for_active_procedure(phase5f_outputs) -> None:
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    open_rows = [r for r in rows if r["status"] == "open"]
    assert open_rows
    assert all(r["noticeId"] == "434619-2026" for r in open_rows)
    assert all(r["status"] != "open" for r in rows if r["noticeId"] != "434619-2026")


def test_everolimus_multi_molecule_estimate_rejected(phase5f_outputs) -> None:
    rows = list(csv.DictReader(phase5f_outputs["output"].open(encoding="utf-8")))
    assert all(r["estimatedValue"] == "" for r in rows if r["noticeId"] == "404973-2025")
