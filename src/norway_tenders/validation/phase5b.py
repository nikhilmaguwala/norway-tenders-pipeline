from __future__ import annotations

import csv
import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from norway_tenders.enrichment.notice_cache import load_canonical_notice
from norway_tenders.extraction.layouts import layout_for_local_file
from norway_tenders.extraction.row_filter import evaluate_pack_for_target
from norway_tenders.models import OutputRow, PackRecord, Provenance
from norway_tenders.normalise.display import normalize_strength_display
from norway_tenders.normalise.lifecycle import build_pack_row, write_output_csv
from norway_tenders.parsers.lis_excel import extract_workbook_buyer, parse_lis_prisskjema
from norway_tenders.settings import OUTPUT_COLUMNS, PROCESSED_DIR, SEEDS_DIR
from norway_tenders.validation.phase5a import discover_seed_files
from norway_tenders.validation.seed_config import PALIPERIDONE_NOTICE_TOTAL_NOK, SEED_FOLDER_META

logger = logging.getLogger(__name__)

OUTPUT_PREVIEW_CSV = PROCESSED_DIR / "output_preview.csv"
PACK_EVIDENCE_CSV = PROCESSED_DIR / "pack_evidence.csv"
ROW_FILTER_AUDIT_CSV = PROCESSED_DIR / "row_filter_audit.csv"
LIFECYCLE_LINKAGE_CSV = PROCESSED_DIR / "lifecycle_linkage.csv"
PHASE5B_QUALITY_JSON = PROCESSED_DIR / "phase5b_quality_report.json"

LIFECYCLE_EXCLUSIONS = [
    {
        "procedureKey": "LIS2234",
        "canonicalNoticeId": "300984-2021",
        "relatedNoticeId": "48506-2021",
        "lifecycleStage": "prior_information",
        "language": "",
        "linkageEvidence": "Phase 4B lifecycle audit",
        "fieldsUsedForEnrichment": "",
        "excludedFromPackOutput": "true",
        "reason": "PIN; canonical pack source is 300984-2021 competition",
    },
    {
        "procedureKey": "LIS2234",
        "canonicalNoticeId": "300984-2021",
        "relatedNoticeId": "147880-2021",
        "lifecycleStage": "prior_information",
        "language": "",
        "linkageEvidence": "Phase 4B lifecycle audit",
        "fieldsUsedForEnrichment": "",
        "excludedFromPackOutput": "true",
        "reason": "PIN duplicate; do not multiply packs",
    },
    {
        "procedureKey": "LIS2234",
        "canonicalNoticeId": "300984-2021",
        "relatedNoticeId": "335380-2021",
        "lifecycleStage": "award",
        "language": "",
        "linkageEvidence": "Phase 4B lifecycle audit",
        "fieldsUsedForEnrichment": "awardedSupplier",
        "excludedFromPackOutput": "true",
        "reason": "Award notice; pack rows sourced from canonical competition documents only",
    },
    {
        "procedureKey": "LIS2234B",
        "canonicalNoticeId": "300984-2021",
        "relatedNoticeId": "335362-2021",
        "lifecycleStage": "revised_competition",
        "language": "",
        "linkageEvidence": "sources.csv LIS 2234b revised competition; local file not supplied",
        "fieldsUsedForEnrichment": "",
        "excludedFromPackOutput": "true",
        "reason": "Revised procurement LIS 2234b; not merged without identical pack-row evidence",
    },
    {
        "procedureKey": "LIS2301D",
        "canonicalNoticeId": "682047-2022",
        "relatedNoticeId": "434619-2026",
        "lifecycleStage": "later_procurement_cycle",
        "language": "",
        "linkageEvidence": "Phase 5C coverage expansion; distinct procurement families",
        "fieldsUsedForEnrichment": "",
        "excludedFromPackOutput": "true",
        "reason": "2601c rows use notice 434619-2026; do not merge into 682047-2022",
    },
    {
        "procedureKey": "2601c",
        "canonicalNoticeId": "434619-2026",
        "relatedNoticeId": "682047-2022",
        "lifecycleStage": "later_procurement_cycle",
        "language": "",
        "linkageEvidence": "Phase 5D local seed Paliperidone__2601c",
        "fieldsUsedForEnrichment": "",
        "excludedFromPackOutput": "false",
        "reason": "Distinct 2026 paliperidone competition; canonical pack source is 434619-2026",
    },
]


@dataclass
class Phase5bResult:
    preview_path: Path
    evidence_path: Path
    audit_path: Path
    lifecycle_path: Path
    quality_path: Path
    row_count: int


def preview_row_key(row: OutputRow, source_row: int | None) -> str:
    procedure = (row.tender_ref or row.notice_id).replace(" ", "").upper()
    if row.item_number:
        return f"{procedure}|{row.product_molecule}|{row.item_number}"
    return f"{procedure}|{row.product_molecule}|{row.source_document}|{source_row or 0}"


def _build_pack_evidence(
    pack: PackRecord,
    target_molecule: str,
    local_file: str,
    filter_result: Any,
) -> dict[str, Any]:
    raw = pack.provenance.raw_values
    volume_year = pack.packs_year
    volume_label = str(pack.provenance.raw_values.get("volume_label") or "")
    if not volume_label and volume_year:
        volume_label = f"PAKNINGER {volume_year}"
    price_type = "max_aip" if pack.max_price is not None else (
        "offered_gip" if pack.offered_gip is not None else ""
    )
    warnings: list[str] = []
    if pack.offered_gip is not None:
        warnings.append("offered_gip_present_not_mapped_to_maxPrice")
    if price_type == "offered_gip":
        warnings.append("gip_only")

    return {
        "localFile": local_file,
        "sheet": pack.provenance.sheet or "",
        "sourceRow": pack.provenance.row or "",
        "targetMolecule": target_molecule,
        "itemNumber": pack.item_number,
        "productName": pack.product_name,
        "rawStrength": raw.get("strength", pack.strength),
        "rawPackDescription": raw.get("pack_size", ""),
        "dosageForm": "",
        "gtin": "",
        "rawSupplier": pack.supplier,
        "rawAtc": pack.atc_code,
        "rawMolecule": raw.get("active_substance", ""),
        "volumeRaw": pack.packs_sold_last_12m,
        "volumePeriodLabel": volume_label,
        "volumePeriodStart": f"{volume_year}-01-01" if volume_year else "",
        "volumePeriodEnd": f"{volume_year}-12-31" if volume_year else "",
        "priceType": price_type,
        "maxPriceRaw": pack.max_price,
        "offeredGip": pack.offered_gip,
        "parserWarnings": "|".join(warnings),
        "matchedTerm": filter_result.matched_term,
        "evidenceLevel": filter_result.evidence_level,
        "detectionMethod": filter_result.match.detection_method if filter_result.match else "",
    }


def _enrich_output_row(
    row: OutputRow,
    notice: Any,
    *,
    source_document: str,
    buyer_from_workbook: str,
    umbrella_estimated: float | None,
) -> OutputRow:
    row.notice_id = notice.notice_id
    row.tender_ref = notice.tender_ref
    row.title = notice.title
    row.buyer = notice.buyer or buyer_from_workbook
    row.notice_type = notice.notice_type
    row.status = ""
    row.publication_date = notice.publication_date
    row.contract_start = notice.contract_start
    row.procedure_type = notice.procedure_type
    row.source_document = source_document
    row.source_url = notice.source_url
    row.currency = notice.currency or "NOK"
    row.estimated_value = None
    row.awarded_value = None
    row.awarded_supplier = ""
    if notice.provenance.raw_values.get("multi_molecule_notice_value"):
        pass
    if umbrella_estimated and umbrella_estimated >= 1_000_000_000:
        pass
    return row


def dedupe_preview_rows(
    rows: list[OutputRow],
    source_rows: list[int | None],
) -> tuple[list[OutputRow], list[int | None]]:
    by_key: dict[str, tuple[OutputRow, int | None]] = {}
    for row, source_row in zip(rows, source_rows, strict=True):
        key = preview_row_key(row, source_row)
        by_key[key] = (row, source_row)
    deduped = list(by_key.values())
    return [item[0] for item in deduped], [item[1] for item in deduped]


def run_phase5b(seeds_root: Path = SEEDS_DIR) -> Phase5bResult:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    audit_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    output_rows: list[OutputRow] = []
    row_source_rows: list[int | None] = []
    rejected_by_file: Counter[str] = Counter()
    notices: dict[str, Any] = {}

    price_files = [
        p for p in discover_seed_files(seeds_root)
        if p.suffix.lower() == ".xlsx" and "prisskjema" in p.name.casefold()
    ]

    for path in price_files:
        local_file = str(path.relative_to(seeds_root))
        folder_key = path.relative_to(seeds_root).parts[0]
        meta = SEED_FOLDER_META[folder_key]
        target = meta.target_molecule

        if meta.notice_id not in notices:
            notices[meta.notice_id] = load_canonical_notice(meta)

        notice = notices[meta.notice_id]
        layout = layout_for_local_file(local_file)
        packs = parse_lis_prisskjema(path, layout=layout)
        buyer_wb = extract_workbook_buyer(path)

        for pack in packs:
            result = evaluate_pack_for_target(pack, target)
            audit_rows.append(
                {
                    "localFile": local_file,
                    "sheet": pack.provenance.sheet or "",
                    "sourceRow": pack.provenance.row or "",
                    "targetMolecule": target,
                    "rawAtc": result.raw_atc,
                    "rawMolecule": result.raw_molecule,
                    "rawProductName": result.raw_product_name,
                    "accepted": str(result.accepted).lower(),
                    "rejectionReason": result.rejection_reason,
                    "matchedTerm": result.matched_term,
                    "detectionMethod": result.match.detection_method if result.match else "",
                    "evidenceLevel": result.evidence_level,
                }
            )
            if not result.accepted:
                rejected_by_file[local_file] += 1
                continue

            assert result.match is not None
            row = build_pack_row(
                notice,
                result.match,
                pack,
                source_document=path.name,
                source_url=notice.source_url,
            )
            row = _enrich_output_row(
                row,
                notice,
                source_document=path.name,
                buyer_from_workbook=buyer_wb,
                umbrella_estimated=notice.estimated_value,
            )
            row.atc_code = pack.atc_code or result.match.atc_code
            row.product_name = pack.product_name
            raw_strength = str(pack.provenance.raw_values.get("strength", pack.strength) or "")
            row.strength = normalize_strength_display(raw_strength or pack.strength)
            if pack.max_price is not None:
                row.max_price = pack.max_price
            elif pack.offered_gip is not None:
                row.max_price = None

            if meta.target_molecule == "Paliperidone":
                row.awarded_value = None
                row.estimated_value = None

            evidence_rows.append(_build_pack_evidence(pack, target, local_file, result))
            output_rows.append(row)
            row_source_rows.append(pack.provenance.row)

    output_rows, row_source_rows = dedupe_preview_rows(output_rows, row_source_rows)

    write_output_csv(output_rows, OUTPUT_PREVIEW_CSV)

    with ROW_FILTER_AUDIT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "localFile", "sheet", "sourceRow", "targetMolecule", "rawAtc", "rawMolecule",
                "rawProductName", "accepted", "rejectionReason", "matchedTerm",
                "detectionMethod", "evidenceLevel",
            ],
        )
        writer.writeheader()
        writer.writerows(audit_rows)

    evidence_fields = list(evidence_rows[0].keys()) if evidence_rows else [
        "localFile", "sheet", "sourceRow", "targetMolecule", "itemNumber", "productName",
        "rawStrength", "rawPackDescription", "dosageForm", "gtin", "rawSupplier", "rawAtc",
        "rawMolecule", "volumeRaw", "volumePeriodLabel", "volumePeriodStart", "volumePeriodEnd",
        "priceType", "maxPriceRaw", "offeredGip", "parserWarnings",
    ]
    with PACK_EVIDENCE_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=evidence_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(evidence_rows)

    with LIFECYCLE_LINKAGE_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "procedureKey", "canonicalNoticeId", "relatedNoticeId", "lifecycleStage",
                "language", "linkageEvidence", "fieldsUsedForEnrichment",
                "excludedFromPackOutput", "reason",
            ],
        )
        writer.writeheader()
        writer.writerows(LIFECYCLE_EXCLUSIONS)

    quality = _build_quality_report(output_rows, audit_rows, rejected_by_file, evidence_rows, row_source_rows)
    PHASE5B_QUALITY_JSON.write_text(json.dumps(quality, indent=2, default=str), encoding="utf-8")

    return Phase5bResult(
        preview_path=OUTPUT_PREVIEW_CSV,
        evidence_path=PACK_EVIDENCE_CSV,
        audit_path=ROW_FILTER_AUDIT_CSV,
        lifecycle_path=LIFECYCLE_LINKAGE_CSV,
        quality_path=PHASE5B_QUALITY_JSON,
        row_count=len(output_rows),
    )


def _build_quality_report(
    rows: list[OutputRow],
    audit_rows: list[dict[str, Any]],
    rejected_by_file: Counter[str],
    evidence_rows: list[dict[str, Any]],
    source_rows: list[int | None],
) -> dict[str, Any]:
    by_molecule: Counter[str] = Counter()
    by_detection: Counter[str] = Counter()
    name_detected = 0
    atc_only = 0
    items: dict[str, set[str]] = defaultdict(set)
    strengths: dict[str, set[str]] = defaultdict(set)
    suppliers: dict[str, set[str]] = defaultdict(set)
    volumes: dict[str, float] = defaultdict(float)
    volume_labels: dict[str, set[str]] = defaultdict(set)
    max_price_cov: dict[str, int] = defaultdict(int)
    missing = Counter()

    for row in rows:
        by_molecule[row.product_molecule] += 1
        by_detection[row.detection_method] += 1
        if row.molecule_detected:
            name_detected += 1
        elif row.detection_method == "atc_in_document":
            atc_only += 1
        if row.item_number:
            items[row.product_molecule].add(row.item_number)
        if row.strength:
            strengths[row.product_molecule].add(row.strength)
        if row.supplier:
            suppliers[row.product_molecule].add(row.supplier)
        if row.max_price is not None:
            max_price_cov[row.product_molecule] += 1
        for field, val in [
            ("maxPrice", row.max_price),
            ("packsSoldLast12m", row.packs_sold_last_12m),
            ("estimatedValue", row.estimated_value),
            ("itemNumber", row.item_number),
        ]:
            if val is None or val == "":
                missing[field] += 1
            elif field == "packsSoldLast12m" and val == 0:
                continue

    for ev in evidence_rows:
        mol = ev["targetMolecule"]
        if ev.get("volumeRaw") is not None:
            volumes[mol] += float(ev["volumeRaw"])
        if ev.get("volumePeriodLabel"):
            volume_labels[mol].add(ev["volumePeriodLabel"])

    keys = [
        preview_row_key(r, sr)
        for r, sr in zip(rows, source_rows, strict=True)
    ]

    return {
        "phase": "5D",
        "accepted_pack_rows_by_molecule": dict(by_molecule),
        "rejected_rows_by_workbook": dict(rejected_by_file),
        "detection_method_counts": dict(by_detection),
        "name_detected_count": name_detected,
        "atc_only_count": atc_only,
        "unique_items_by_molecule": {k: sorted(v) for k, v in items.items()},
        "unique_strengths_by_molecule": {k: sorted(v) for k, v in strengths.items()},
        "unique_suppliers_by_molecule": {k: sorted(v) for k, v in suppliers.items()},
        "volume_totals_by_molecule": dict(volumes),
        "volume_period_labels_by_molecule": {k: sorted(v) for k, v in volume_labels.items()},
        "max_price_coverage_by_molecule": dict(max_price_cov),
        "estimated_value_coverage": sum(1 for r in rows if r.estimated_value is not None),
        "awarded_value_coverage": sum(1 for r in rows if r.awarded_value is not None),
        "missingness_percent": {
            k: round(100 * v / max(len(rows), 1), 1) for k, v in missing.items()
        },
        "duplicate_keys_removed": len(rows) - len(set(keys)),
        "lifecycle_records_excluded": len(LIFECYCLE_EXCLUSIONS),
        "value_allocation_warnings": [
            f"Paliperidone notice total NOK {PALIPERIDONE_NOTICE_TOTAL_NOK:,} not allocated to molecule rows",
            "LIS 2207 umbrella estimated value NOK 3.2bn not allocated to Axitinib pack rows",
            "Offered GIP never mapped to maxPrice",
            "Everolimus 2632a excludes mycophenolic acid L04AA06 rows",
            "LIS 2301d prisskjema filtered to N05AX13 / paliperidon only",
            "2601c paliperidone (434619-2026) is distinct from LIS 2301d; repeated products retained across procedures",
        ],
        "preview_row_count": len(rows),
        "within_40_120_rows": 40 <= len(rows) <= 120,
        "below_40_explanation": (
            f"Only defensible target-attributed pack rows are included ({len(rows)} rows). "
            "Additional procedures would be needed to reach 40 without inventing rows; "
            "assignment preference is quality over completeness."
        ) if len(rows) < 40 else "",
        "representative_preview_rows": _representative_rows(rows),
        "issues_requiring_human_decision": [
            "Axitinib confirmed by ATC L01EK01 + Inlyta brand only; no INN name in source rows",
            "Paliperidone awardedSupplier left blank; Nordic Pill AB documented at notice level only",
            "Anagrelide Xagrid brand rows excluded (brand-only without ATC/name)",
            "Volume fields use calendar-year PAKNINGER YYYY proxy, not verified 12-month rolling period",
            "Everolimus maxPrice present but mycophenolic acid rows excluded by design",
        ],
        "validation": validate_preview(rows),
    }


def _representative_rows(rows: list[OutputRow]) -> list[dict[str, Any]]:
    picks: list[OutputRow] = []
    for molecule in ["Axitinib", "Paliperidone", "Lenalidomide", "Everolimus", "Anagrelide"]:
        for row in rows:
            if row.product_molecule == molecule:
                picks.append(row)
                break
    return [r.to_csv_dict() for r in picks[:5]]


def validate_preview(rows: list[OutputRow]) -> dict[str, Any]:
    errors: list[str] = []
    molecules = {r.product_molecule for r in rows}
    keys: set[str] = set()

    for i, row in enumerate(rows):
        d = row.to_csv_dict()
        if list(d.keys()) != OUTPUT_COLUMNS:
            errors.append(f"Row {i}: column schema mismatch")

        if row.product_molecule not in {
            "Axitinib", "Everolimus", "Lenalidomide", "Anagrelide", "Paliperidone",
        }:
            errors.append(f"Row {i}: invalid molecule")

        if row.detection_method not in {"name_in_document", "atc_in_document"}:
            errors.append(f"Row {i}: missing accepted detection method")

        if row.molecule_detected and row.detection_method != "name_in_document":
            errors.append(f"Row {i}: moleculeDetected without name method")

        if row.awarded_value == PALIPERIDONE_NOTICE_TOTAL_NOK:
            errors.append(f"Row {i}: Paliperidone notice total incorrectly allocated")

        if row.estimated_value and row.estimated_value >= 3_000_000_000:
            errors.append(f"Row {i}: umbrella value allocated to pack row")

        if not row.source_document or not row.source_url:
            errors.append(f"Row {i}: missing source provenance")

        if row.source_url.startswith("file://"):
            errors.append(f"Row {i}: synthetic URL")

        if isinstance(row.item_number, str) is False:
            errors.append(f"Row {i}: itemNumber not string")

        if row.pack_size is not None and not isinstance(row.pack_size, (int, float)):
            errors.append(f"Row {i}: packSize not numeric")

        key = preview_row_key(row, None)
        if key in keys:
            errors.append(f"Row {i}: duplicate key {key}")
        keys.add(key)

    required_molecules = {"Axitinib", "Everolimus", "Lenalidomide", "Anagrelide", "Paliperidone"}
    if not required_molecules.issubset(molecules):
        errors.append(f"Missing molecules: {required_molecules - molecules}")

    return {"errors": errors, "passed": not errors}
