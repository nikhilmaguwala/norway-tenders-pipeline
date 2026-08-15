from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from norway_tenders.enrichment.dmp_prices import (
  DMP_PUBLISHER,
  DMP_SOURCE_URL,
  MAX_PRICE_DEFINITION,
  discover_dmp_workbook,
  join_pack_to_dmp,
  load_dmp_price_index,
)
from norway_tenders.enrichment.volume_semantics import volume_semantics_for_source
from norway_tenders.extraction.layouts import layout_for_local_file
from norway_tenders.models import OutputRow
from norway_tenders.normalise.display import normalize_pack_volume
from norway_tenders.normalise.lifecycle import write_output_csv
from norway_tenders.parsers.lis_excel import parse_lis_prisskjema
from norway_tenders.settings import OUTPUT_COLUMNS, PROCESSED_DIR, SEEDS_DIR, SOURCES_SEED
from norway_tenders.validation.phase5b import preview_row_key, validate_preview
from norway_tenders.validation.seed_config import PALIPERIDONE_NOTICE_TOTAL_NOK

logger = logging.getLogger(__name__)

OUTPUT_PREVIEW_CSV = PROCESSED_DIR / "output_preview.csv"
OUTPUT_FINAL_CANDIDATE_CSV = PROCESSED_DIR / "output_final_candidate.csv"
PACK_EVIDENCE_CSV = PROCESSED_DIR / "pack_evidence.csv"
DMP_PRICE_JOIN_AUDIT_CSV = PROCESSED_DIR / "dmp_price_join_audit.csv"
PHASE5E_QUALITY_JSON = PROCESSED_DIR / "phase5e_quality_report.json"

DMP_AUDIT_COLUMNS = [
  "productMolecule", "noticeId", "tenderRef", "itemNumber", "productName", "strength",
  "packSize", "sourceAtc", "dmpMatchedItem", "dmpProductName", "dmpStrength", "dmpPack",
  "dmpAtc", "tenderDocumentMaxAip", "dmpMaxAip", "selectedMaxPrice", "maxPriceSource",
  "maxPriceEffectiveDate", "joinOutcome", "conflictReason",
]

SOURCES_EXTRA_COLUMNS = ["publisher", "effectiveDate"]


@dataclass
class Phase5eResult:
  preview_path: Path
  final_candidate_path: Path
  evidence_path: Path
  dmp_audit_path: Path
  quality_path: Path
  row_count: int


def _parse_float(value: str) -> float | None:
  text = (value or "").strip()
  if not text:
    return None
  return float(text)


def _csv_row_to_output(row: dict[str, str]) -> OutputRow:
  return OutputRow(
    notice_id=row["noticeId"],
    tender_ref=row["tenderRef"],
    title=row["title"],
    country=row["country"] or "NO",
    buyer=row["buyer"],
    product_molecule=row["productMolecule"],
    molecule_detected=row["moleculeDetected"] == "True",
    molecule_variant=row["moleculeVariant"],
    detection_method=row["detectionMethod"],
    atc_code=row["atcCode"],
    item_number=row["itemNumber"],
    product_name=row["productName"],
    strength=row["strength"],
    pack_size=_parse_float(row["packSize"]) if row.get("packSize") else None,
    supplier=row["supplier"],
    max_price=_parse_float(row["maxPrice"]) if row.get("maxPrice") else None,
    packs_sold_last_12m=(
      _parse_float(row["packsSoldLast12m"]) if row.get("packsSoldLast12m") != "" else None
    ),
    estimated_value=_parse_float(row["estimatedValue"]) if row.get("estimatedValue") else None,
    awarded_value=_parse_float(row["awardedValue"]) if row.get("awardedValue") else None,
    awarded_supplier=row["awardedSupplier"],
    currency=row["currency"] or "NOK",
    notice_type=row["noticeType"],
    status=row["status"],
    publication_date=row["publicationDate"],
    contract_start=row["contractStart"],
    procedure_type=row["procedureType"],
    source_document=row["sourceDocument"],
    source_url=row["sourceUrl"],
  )


def _output_to_csv_dict(row: OutputRow) -> dict[str, Any]:
  data = row.to_csv_dict()
  if row.packs_sold_last_12m == 0:
    data["packsSoldLast12m"] = 0
  return data


def _load_volume_overrides(seeds_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
  """Re-read prisskjema volumes with corrected zero handling."""
  overrides: dict[tuple[str, str], dict[str, Any]] = {}
  for path in sorted(seeds_root.rglob("*Prisskjema*.xlsx")):
    rel_name = path.name
    local_file = str(path.relative_to(seeds_root))
    layout = layout_for_local_file(local_file)
    try:
      packs = parse_lis_prisskjema(path, layout=layout)
    except Exception:
      continue
    for pack in packs:
      key = (rel_name, pack.item_number)
      semantics = volume_semantics_for_source(
        rel_name,
        volume_label=str(pack.provenance.raw_values.get("volume_label") or ""),
        packs_year=pack.provenance.raw_values.get("packs_year"),
      )
      volume = pack.packs_sold_last_12m
      if not semantics.populate_packs_sold_last_12m:
        volume = None
      overrides[key] = {
        "packs_sold_last_12m": volume,
        "volume_semantics": semantics,
        "source_row": pack.provenance.row,
        "volume_warning": pack.provenance.raw_values.get("volume_warning", ""),
      }
  return overrides


def _read_existing_sources() -> tuple[list[str], list[dict[str, str]]]:
  if not SOURCES_SEED.exists():
    return [], []
  with SOURCES_SEED.open(encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    fieldnames = list(reader.fieldnames or [])
    rows = [dict(row) for row in reader]
  return fieldnames, rows


def _update_sources_with_dmp(index: Any, seeds_root: Path) -> None:
  fieldnames, rows = _read_existing_sources()
  for col in SOURCES_EXTRA_COLUMNS:
    if col not in fieldnames:
      fieldnames.append(col)

  dmp_local = index.local_file
  updated = False
  for row in rows:
    if row.get("sourceType") == "official_maximum_price_reference" or row.get("filename") == index.filename:
      row["localFile"] = dmp_local
      row["publisher"] = DMP_PUBLISHER
      row["effectiveDate"] = index.effective_date
      row["sha256"] = index.sha256
      updated = True

  if not updated:
    rows.append({
      "tenderRef": "",
      "noticeId": "",
      "filename": index.filename,
      "url": DMP_SOURCE_URL,
      "source": "dmp_reference",
      "notes": MAX_PRICE_DEFINITION,
      "targetMolecule": "",
      "localFile": dmp_local,
      "sourceType": "official_maximum_price_reference",
      "accessStatus": "downloaded_manually",
      "noticeUrl": DMP_SOURCE_URL,
      "landingPage": DMP_SOURCE_URL,
      "linkageNeedsReview": "false",
      "procurementFamily": "DMP",
      "sha256": index.sha256,
      "publisher": DMP_PUBLISHER,
      "effectiveDate": index.effective_date,
    })

  with SOURCES_SEED.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def _load_pack_evidence() -> list[dict[str, Any]]:
  if not PACK_EVIDENCE_CSV.exists():
    return []
  with PACK_EVIDENCE_CSV.open(encoding="utf-8", newline="") as fh:
    return list(csv.DictReader(fh))


def _write_pack_evidence(rows: list[dict[str, Any]]) -> None:
  if not rows:
    return
  fieldnames = list(rows[0].keys())
  extra = [
    "volumePeriodLabel", "volumePeriodStart", "volumePeriodEnd",
    "volumeIsTwelveMonths", "volumeInterpretationWarning", "volumeEvidence",
    "maxPriceSource", "maxPriceEffectiveDate", "tenderDocumentMaxAip", "dmpMaxAip",
    "priceDifference", "priceDifferencePct", "dmpJoinOutcome", "dmpTemporalWarning",
  ]
  for col in extra:
    if col not in fieldnames:
      fieldnames.append(col)
  with PACK_EVIDENCE_CSV.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def run_phase5e(
  *,
  seeds_root: Path = SEEDS_DIR,
  preview_path: Path = OUTPUT_PREVIEW_CSV,
) -> Phase5eResult:
  PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

  preview_rows_raw = list(csv.DictReader(preview_path.open(encoding="utf-8")))
  rows = [_csv_row_to_output(r) for r in preview_rows_raw]
  volume_overrides = _load_volume_overrides(seeds_root)

  dmp_path = discover_dmp_workbook(seeds_root)
  dmp_index = load_dmp_price_index(dmp_path, seeds_root=seeds_root)
  _update_sources_with_dmp(dmp_index, seeds_root)

  evidence_rows = _load_pack_evidence()
  evidence_by_key: dict[tuple[str, str], dict[str, Any]] = {}
  for r in evidence_rows:
    local = r.get("localFile", "")
    doc_name = Path(local).name if local else ""
    evidence_by_key[(doc_name, r.get("itemNumber", ""))] = r

  audit_rows: list[dict[str, Any]] = []
  max_price_before = sum(1 for r in rows if r.max_price is not None)
  zeros_restored = 0

  dmp_temporal_warning = (
    f"DMP maximum AIP effective {dmp_index.effective_date} is a current-reference enrichment; "
    "not tender-time maximum price for historical procedures."
  )

  for row in rows:
    override = volume_overrides.get((row.source_document, row.item_number), {})
    if override:
      new_volume = override.get("packs_sold_last_12m")
      if new_volume == 0 and row.packs_sold_last_12m is None:
        zeros_restored += 1
      row.packs_sold_last_12m = new_volume
      semantics = override.get("volume_semantics")
    else:
      semantics = volume_semantics_for_source(row.source_document)

    tender_doc_aip = row.max_price
    join = join_pack_to_dmp(
      index=dmp_index,
      item_number=row.item_number,
      product_molecule=row.product_molecule,
      source_atc=row.atc_code,
      product_name=row.product_name,
      strength=row.strength,
      pack_size=row.pack_size,
      tender_document_max_aip=tender_doc_aip,
    )
    row.max_price = join.selected_max_price

    dmp_row = join.dmp_row
    audit_rows.append({
      "productMolecule": row.product_molecule,
      "noticeId": row.notice_id,
      "tenderRef": row.tender_ref,
      "itemNumber": row.item_number,
      "productName": row.product_name,
      "strength": row.strength,
      "packSize": row.pack_size if row.pack_size is not None else "",
      "sourceAtc": row.atc_code,
      "dmpMatchedItem": join.dmp_matched_item,
      "dmpProductName": dmp_row.product_name if dmp_row else "",
      "dmpStrength": dmp_row.strength if dmp_row else "",
      "dmpPack": dmp_row.pack_description if dmp_row else "",
      "dmpAtc": dmp_row.atc_code if dmp_row else "",
      "tenderDocumentMaxAip": join.tender_document_max_aip if join.tender_document_max_aip is not None else "",
      "dmpMaxAip": join.dmp_max_aip if join.dmp_max_aip is not None else "",
      "selectedMaxPrice": join.selected_max_price if join.selected_max_price is not None else "",
      "maxPriceSource": join.max_price_source,
      "maxPriceEffectiveDate": join.max_price_effective_date,
      "joinOutcome": join.join_outcome,
      "conflictReason": join.conflict_reason,
    })

    ev_key = (row.source_document, row.item_number)
    ev = evidence_by_key.get(ev_key)
    if ev is None:
      ev = {
        "localFile": row.source_document,
        "itemNumber": row.item_number,
        "targetMolecule": row.product_molecule,
        "productName": row.product_name,
      }
      evidence_by_key[ev_key] = ev

    if semantics:
      ev["volumePeriodLabel"] = semantics.volume_period_label
      ev["volumePeriodStart"] = semantics.volume_period_start
      ev["volumePeriodEnd"] = semantics.volume_period_end
      ev["volumeIsTwelveMonths"] = str(semantics.volume_is_twelve_months).lower()
      ev["volumeInterpretationWarning"] = semantics.volume_interpretation_warning
      ev["volumeEvidence"] = semantics.evidence
    ev["volumeRaw"] = row.packs_sold_last_12m if row.packs_sold_last_12m is not None else ""
    ev["maxPriceSource"] = join.max_price_source
    ev["maxPriceEffectiveDate"] = join.max_price_effective_date
    ev["tenderDocumentMaxAip"] = join.tender_document_max_aip if join.tender_document_max_aip is not None else ""
    ev["dmpMaxAip"] = join.dmp_max_aip if join.dmp_max_aip is not None else ""
    ev["priceDifference"] = join.price_difference if join.price_difference is not None else ""
    ev["priceDifferencePct"] = join.price_difference_pct if join.price_difference_pct is not None else ""
    ev["dmpJoinOutcome"] = join.join_outcome
    ev["dmpTemporalWarning"] = dmp_temporal_warning if join.max_price_source == "dmp_current_reference" else ""

  max_price_after = sum(1 for r in rows if r.max_price is not None)

  validation = validate_preview(rows)
  write_output_csv(rows, OUTPUT_FINAL_CANDIDATE_CSV)

  with DMP_PRICE_JOIN_AUDIT_CSV.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=DMP_AUDIT_COLUMNS)
    writer.writeheader()
    writer.writerows(audit_rows)

  _write_pack_evidence(list(evidence_by_key.values()))

  join_outcomes = Counter(r["joinOutcome"] for r in audit_rows)
  price_sources = Counter(r["maxPriceSource"] for r in audit_rows if r["maxPriceSource"])
  by_molecule_join = Counter((r["productMolecule"], r["joinOutcome"]) for r in audit_rows)
  by_molecule = Counter(r.product_molecule for r in rows)
  volume_totals = Counter()
  for row in rows:
    if row.packs_sold_last_12m is not None:
      volume_totals[row.product_molecule] += float(row.packs_sold_last_12m)

  missing = {
    "maxPrice": sum(1 for r in rows if r.max_price is None),
    "packsSoldLast12m": sum(1 for r in rows if r.packs_sold_last_12m is None),
    "estimatedValue": sum(1 for r in rows if r.estimated_value is None),
    "awardedValue": sum(1 for r in rows if r.awarded_value is None),
    "awardedSupplier": sum(1 for r in rows if not r.awarded_supplier),
  }
  missing_pct = {k: round(100 * v / max(len(rows), 1), 1) for k, v in missing.items()}

  price_diffs = []
  for r in audit_rows:
    tender = r.get("tenderDocumentMaxAip")
    dmp = r.get("dmpMaxAip")
    if tender in ("", None) or dmp in ("", None):
      continue
    diff = round(float(dmp) - float(tender), 2)
    if diff != 0:
      price_diffs.append({**r, "priceDifference": diff})

  quality = {
    "phase": "5E",
    "max_price_definition": MAX_PRICE_DEFINITION,
    "dmp_workbook": {
      "localFile": dmp_index.local_file,
      "filename": dmp_index.filename,
      "sha256": dmp_index.sha256,
      "effectiveDate": dmp_index.effective_date,
      "sourceUrl": DMP_SOURCE_URL,
      "publisher": DMP_PUBLISHER,
      "sheet": dmp_index.sheet_name,
      "headerRow": dmp_index.header_row,
      "rowCount": len(dmp_index.by_varenummer),
    },
    "pakningssalg_semantics": volume_semantics_for_source("2601c_Bilag_2_Prisskjema.xlsx").__dict__,
    "explicit_zero_volumes_restored": zeros_restored,
    "preview_row_count_unchanged": len(preview_rows_raw),
    "final_candidate_row_count": len(rows),
    "max_price_coverage_before": max_price_before,
    "max_price_coverage_after": max_price_after,
    "price_source_counts": dict(price_sources),
    "join_outcome_counts": dict(join_outcomes),
    "join_outcomes_by_molecule": {
      f"{mol}|{outcome}": count for (mol, outcome), count in by_molecule_join.items()
    },
    "final_rows_by_molecule": dict(by_molecule),
    "volume_totals_by_molecule": dict(volume_totals),
    "missingness": missing,
    "missingness_percent": missing_pct,
    "tender_vs_dmp_price_differences": price_diffs,
    "dmp_temporal_warning": dmp_temporal_warning,
    "value_allocation_warnings": [
      f"Paliperidone notice total NOK {PALIPERIDONE_NOTICE_TOTAL_NOK:,} not allocated",
      "LIS 2207 umbrella estimated value not allocated to Axitinib rows",
      "Offered GIP never mapped to maxPrice",
    ],
    "validation": validation,
  }
  PHASE5E_QUALITY_JSON.write_text(json.dumps(quality, indent=2, default=str), encoding="utf-8")

  return Phase5eResult(
    preview_path=preview_path,
    final_candidate_path=OUTPUT_FINAL_CANDIDATE_CSV,
    evidence_path=PACK_EVIDENCE_CSV,
    dmp_audit_path=DMP_PRICE_JOIN_AUDIT_CSV,
    quality_path=PHASE5E_QUALITY_JSON,
    row_count=len(rows),
  )
