from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from norway_tenders.enrichment.dmp_prices import (
  DMP_SOURCE_URL,
  MAX_PRICE_DEFINITION,
  DmpPackPrice,
  DmpPriceIndex,
  discover_dmp_workbook,
  join_pack_to_dmp,
  load_dmp_price_index,
)
from norway_tenders.enrichment.volume_semantics import volume_semantics_for_source
from norway_tenders.normalise.display import normalize_pack_volume, parse_pack_volume
from norway_tenders.settings import OUTPUT_COLUMNS, PROCESSED_DIR, SEEDS_DIR
from norway_tenders.validation.phase5e import run_phase5e

ZERO_ITEMS_2601C = {
  "343892", "445725", "536742", "549759", "47538", "392171",
}


@pytest.fixture(scope="module")
def phase5e_outputs() -> dict[str, Path]:
  result = run_phase5e()
  return {
    "preview": result.preview_path,
    "final": result.final_candidate_path,
    "audit": result.dmp_audit_path,
    "quality": result.quality_path,
  }


def test_explicit_source_zero_remains_numeric_zero() -> None:
  assert normalize_pack_volume(0) == 0
  assert normalize_pack_volume("0") == 0
  assert parse_pack_volume(0) == (0, "")
  assert parse_pack_volume("0.0") == (0, "")


def test_blank_volume_remains_blank() -> None:
  assert normalize_pack_volume(None) is None
  assert normalize_pack_volume("") is None
  assert normalize_pack_volume("#N/A") is None
  assert parse_pack_volume("")[0] is None


def test_malformed_volume_text_warning() -> None:
  value, warning = parse_pack_volume("abc")
  assert value is None
  assert warning == "malformed_volume_text"


def test_no_other_field_converts_blank_to_zero() -> None:
  assert normalize_pack_volume(None) is None
  assert normalize_pack_volume("-") is None


def test_zero_not_counted_in_missingness_percent(phase5e_outputs) -> None:
  quality = json.loads(phase5e_outputs["quality"].read_text(encoding="utf-8"))
  final = list(csv.DictReader(phase5e_outputs["final"].open(encoding="utf-8")))
  zero_rows = [r for r in final if r["packsSoldLast12m"] in {"0", "0.0"}]
  assert zero_rows
  assert quality["missingness"]["packsSoldLast12m"] < len(final)


def test_zero_contributes_zero_to_aggregate_volume(phase5e_outputs) -> None:
  final = list(csv.DictReader(phase5e_outputs["final"].open(encoding="utf-8")))
  pali_2601c = [
    r for r in final
    if r["productMolecule"] == "Paliperidone" and r["noticeId"] == "434619-2026"
  ]
  total = sum(float(r["packsSoldLast12m"] or 0) for r in pali_2601c)
  assert total == 8791.0


def test_pakningssalg_period_semantics() -> None:
  sem = volume_semantics_for_source("2601c_Bilag_2_Prisskjema.xlsx")
  assert sem.volume_is_twelve_months is True
  assert sem.populate_packs_sold_last_12m is True
  assert "Sykehusapotekenes legemiddelstatistikk" in sem.evidence
  assert "siste 12 mnd" in sem.volume_period_label


def test_dmp_workbook_detection() -> None:
  path = discover_dmp_workbook(SEEDS_DIR)
  assert path.name == "legemiddelpriser-2026-08-03.xlsx"
  index = load_dmp_price_index(path, seeds_root=SEEDS_DIR)
  assert index.effective_date == "2026-08-03"
  assert index.sha256
  assert len(index.by_varenummer) > 1000


def test_exact_item_number_match_with_leading_zero_padding() -> None:
  path = discover_dmp_workbook(SEEDS_DIR)
  index = load_dmp_price_index(path, seeds_root=SEEDS_DIR)
  join = join_pack_to_dmp(
    index=index,
    item_number="14917",
    product_molecule="Everolimus",
    source_atc="L04AH02",
    product_name="Certican disperg tab 0,25mg",
    strength="0.25 mg",
    pack_size=60,
    tender_document_max_aip=1017.61,
  )
  assert join.join_outcome == "exact_validated"
  assert join.dmp_matched_item == "014917"


def test_atc_conflict_rejection() -> None:
  index = DmpPriceIndex(
    local_file="test.xlsx",
    filename="test.xlsx",
    sha256="x",
    effective_date="2026-08-03",
    sheet_name="Sheet1",
    header_row=3,
    by_varenummer={
      "123": DmpPackPrice("123", "X", "1 mg", "", "L01EK01", 100.0, "2026-08-03"),
    },
    stripped_to_varenummer={"123": ["123"]},
  )
  join = join_pack_to_dmp(
    index=index,
    item_number="123",
    product_molecule="Axitinib",
    source_atc="L04AX04",
    product_name="Test",
    strength="1 mg",
    pack_size=1,
    tender_document_max_aip=None,
  )
  assert join.join_outcome == "exact_item_conflicting_attributes"
  assert join.selected_max_price is None


def test_strength_conflict_warning() -> None:
  index = DmpPriceIndex(
    local_file="test.xlsx",
    filename="test.xlsx",
    sha256="x",
    effective_date="2026-08-03",
    sheet_name="Sheet1",
    header_row=3,
    by_varenummer={
      "999": DmpPackPrice("999", "Inlyta", "7 mg", "", "L01EK01", 100.0, "2026-08-03"),
    },
    stripped_to_varenummer={"999": ["999"]},
  )
  join = join_pack_to_dmp(
    index=index,
    item_number="999",
    product_molecule="Axitinib",
    source_atc="L01EK01",
    product_name="Inlyta tab 1mg",
    strength="1 mg",
    pack_size=56,
    tender_document_max_aip=None,
  )
  assert join.join_outcome == "exact_item_conflicting_attributes"
  assert "strength" in join.conflict_reason


def test_no_fuzzy_item_matching() -> None:
  path = discover_dmp_workbook(SEEDS_DIR)
  index = load_dmp_price_index(path, seeds_root=SEEDS_DIR)
  join = join_pack_to_dmp(
    index=index,
    item_number="999999999",
    product_molecule="Axitinib",
    source_atc="L01EK01",
    product_name="Inlyta",
    strength="1 mg",
    pack_size=1,
    tender_document_max_aip=None,
  )
  assert join.join_outcome == "no_item_match"


def test_price_source_precedence_tender_over_dmp(phase5e_outputs) -> None:
  audit = list(csv.DictReader(phase5e_outputs["audit"].open(encoding="utf-8")))
  ever = [r for r in audit if r["productMolecule"] == "Everolimus"]
  assert ever
  assert all(r["maxPriceSource"] == "tender_document" for r in ever)
  assert all(r["selectedMaxPrice"] == r["tenderDocumentMaxAip"] for r in ever)


def test_dmp_temporal_provenance(phase5e_outputs) -> None:
  quality = json.loads(phase5e_outputs["quality"].read_text(encoding="utf-8"))
  assert "current-reference" in quality["dmp_temporal_warning"]
  assert quality["dmp_workbook"]["effectiveDate"] == "2026-08-03"
  audit = list(csv.DictReader(phase5e_outputs["audit"].open(encoding="utf-8")))
  ax = [r for r in audit if r["productMolecule"] == "Axitinib"][0]
  assert ax["maxPriceSource"] == "dmp_current_reference"
  assert ax["maxPriceEffectiveDate"] == "2026-08-03"


def test_max_price_definition_documented(phase5e_outputs) -> None:
  quality = json.loads(phase5e_outputs["quality"].read_text(encoding="utf-8"))
  assert "maximum apotekinnkjøpspris (AIP)" in quality["max_price_definition"]
  assert "AUP" in quality["max_price_definition"]


def test_final_candidate_41_rows_stable(phase5e_outputs) -> None:
  rows = list(csv.DictReader(phase5e_outputs["final"].open(encoding="utf-8")))
  assert len(rows) == 41


def test_preview_unchanged(phase5e_outputs) -> None:
  preview_path = phase5e_outputs["preview"]
  before = preview_path.read_bytes()
  run_phase5e()
  after = preview_path.read_bytes()
  assert before == after


def test_explicit_zero_volumes_restored_in_final(phase5e_outputs) -> None:
  rows = list(csv.DictReader(phase5e_outputs["final"].open(encoding="utf-8")))
  restored = {
    r["itemNumber"]
    for r in rows
    if r["noticeId"] == "434619-2026" and r["packsSoldLast12m"] in {"0", "0.0"}
  }
  assert ZERO_ITEMS_2601C.issubset(restored)


def test_full_schema_validation(phase5e_outputs) -> None:
  rows = list(csv.DictReader(phase5e_outputs["final"].open(encoding="utf-8")))
  assert list(rows[0].keys()) == OUTPUT_COLUMNS
  quality = json.loads(phase5e_outputs["quality"].read_text(encoding="utf-8"))
  assert quality["validation"]["passed"] is True


def test_axitinib_dmp_enrichment(phase5e_outputs) -> None:
  rows = list(csv.DictReader(phase5e_outputs["final"].open(encoding="utf-8")))
  ax = [r for r in rows if r["productMolecule"] == "Axitinib"]
  assert len(ax) == 4
  assert all(r["maxPrice"] for r in ax)


def test_sources_csv_contains_dmp_reference() -> None:
  rows = list(csv.DictReader((SEEDS_DIR / "sources.csv").open(encoding="utf-8")))
  dmp = [r for r in rows if r.get("sourceType") == "official_maximum_price_reference"]
  assert dmp
  assert dmp[0]["url"] == DMP_SOURCE_URL
  assert dmp[0]["publisher"] == "Direktoratet for medisinske produkter"
