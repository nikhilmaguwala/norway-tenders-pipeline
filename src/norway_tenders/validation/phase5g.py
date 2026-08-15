from __future__ import annotations

import csv
import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from norway_tenders.models import OutputRow
from norway_tenders.normalise.export_semantics import (
    apply_semantic_cleanup_to_row,
    clean_pack_size_for_export,
    clean_supplier_for_export,
    map_notice_type,
    map_procedure_type,
    row_to_export_dict,
)
from norway_tenders.settings import OUTPUT_COLUMNS, OUTPUT_CSV, PROCESSED_DIR
from norway_tenders.validation.phase5e import _csv_row_to_output
from norway_tenders.validation.phase5f import run_phase5f, validate_final_output

logger = logging.getLogger(__name__)

PACK_EVIDENCE_CSV = PROCESSED_DIR / "pack_evidence.csv"
SEMANTIC_CLEANUP_AUDIT_CSV = PROCESSED_DIR / "final_semantic_cleanup_audit.csv"
PHASE5G_QUALITY_JSON = PROCESSED_DIR / "phase5g_quality_report.json"

AUDIT_COLUMNS = [
    "noticeId", "itemNumber", "field", "rawValue", "cleanedValue",
    "action", "reason", "sourceDocument", "sourceRow",
]

PACK_EVIDENCE_EXTRA_COLUMNS = [
    "supplierExportDecision",
    "supplierRejectionReason",
    "packSizeValidityDecision",
    "packSizeExportWarning",
    "rawNoticeType",
    "rawProcedureType",
]

FORBIDDEN_OUTPUT_FRAGMENTS = ("#N/A", "#VALUE!", "#REF!", "Grossister vgr 6")
FORBIDDEN_NOTICE_TYPES = frozenset({"3", "V", "cn-standard"})
FORBIDDEN_PROCEDURE_TYPES = frozenset({"1", "V", "open"})


@dataclass
class Phase5gResult:
    output_path: Path
    audit_path: Path
    evidence_path: Path
    quality_path: Path
    row_count: int
    output_sha256: str


def _load_rows_from_csv(path: Path) -> list[OutputRow]:
    return [_csv_row_to_output(row) for row in csv.DictReader(path.open(encoding="utf-8"))]


def _load_pack_evidence() -> list[dict[str, Any]]:
    if not PACK_EVIDENCE_CSV.exists():
        return []
    with PACK_EVIDENCE_CSV.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _evidence_key(local_file: str, item_number: str) -> tuple[str, str]:
    return Path(local_file).name, item_number


def _update_pack_evidence(
    evidence_rows: list[dict[str, Any]],
    row: OutputRow,
    *,
    raw_notice_type: str,
    raw_procedure_type: str,
) -> None:
    doc_name = row.source_document
    raw_supplier = ""
    raw_pack = ""
    source_row = ""
    for ev in evidence_rows:
        if _evidence_key(ev.get("localFile", ""), ev.get("itemNumber", "")) == (doc_name, row.item_number):
            raw_supplier = str(ev.get("rawSupplier") or "")
            raw_pack = str(ev.get("rawPackDescription") or "")
            source_row = str(ev.get("sourceRow") or "")
            cleaned_supplier, supplier_reason = clean_supplier_for_export(raw_supplier)
            try:
                pack_from_raw = float(str(raw_pack).replace(",", ".")) if raw_pack not in ("", "#N/A", "N/A") else None
            except ValueError:
                pack_from_raw = None
            _, pack_warning = clean_pack_size_for_export(pack_from_raw)

            ev["supplierExportDecision"] = "exported" if cleaned_supplier else "blanked"
            ev["supplierRejectionReason"] = supplier_reason
            ev["packSizeValidityDecision"] = "valid" if not pack_warning else "invalid"
            ev["packSizeExportWarning"] = pack_warning
            ev["rawNoticeType"] = raw_notice_type
            ev["rawProcedureType"] = raw_procedure_type

            warnings = [w for w in str(ev.get("parserWarnings") or "").split("|") if w]
            if supplier_reason == "spreadsheet_error_placeholder":
                warnings.append("supplier_spreadsheet_error_not_exported")
            elif supplier_reason == "supplier_grouping_label_not_legal_entity":
                warnings.append("supplier_grouping_label_not_exported")
            if pack_warning == "invalid_zero_pack_size":
                warnings.append("invalid_zero_pack_size")
            elif pack_warning == "negative_pack_size":
                warnings.append("negative_pack_size_not_exported")
            ev["parserWarnings"] = "|".join(dict.fromkeys(warnings))
            return

    logger.warning("No pack evidence for %s item %s", doc_name, row.item_number)


def _attach_source_rows(audit_rows: list[dict[str, Any]], evidence_rows: list[dict[str, Any]]) -> None:
    by_key = {
        _evidence_key(ev.get("localFile", ""), ev.get("itemNumber", "")): ev.get("sourceRow", "")
        for ev in evidence_rows
    }
    for entry in audit_rows:
        key = (entry.get("sourceDocument", ""), entry.get("itemNumber", ""))
        entry["sourceRow"] = by_key.get(key, "")


def validate_semantic_output(rows: list[OutputRow], export_text: str) -> dict[str, Any]:
    errors: list[str] = []
    base = validate_final_output(rows)

    if len(rows) != 41:
        errors.append(f"Expected 41 rows, got {len(rows)}")

    for fragment in FORBIDDEN_OUTPUT_FRAGMENTS:
        if fragment in export_text:
            errors.append(f"Forbidden fragment in output: {fragment}")

    for row in rows:
        if row.notice_type in FORBIDDEN_NOTICE_TYPES:
            errors.append(f"Raw noticeType {row.notice_type!r} on {row.notice_id}")
        if row.procedure_type in FORBIDDEN_PROCEDURE_TYPES:
            errors.append(f"Raw procedureType {row.procedure_type!r} on {row.notice_id}")
        if row.pack_size is not None and float(row.pack_size) <= 0:
            errors.append(f"Invalid packSize {row.pack_size} on {row.item_number}")

    by_notice: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        bucket = by_notice.setdefault(row.notice_id, {"noticeType": set(), "procedureType": set()})
        bucket["noticeType"].add(row.notice_type or "")
        bucket["procedureType"].add(row.procedure_type or "")

    for notice_id, fields in by_notice.items():
        if len(fields["noticeType"]) > 1:
            errors.append(f"Inconsistent noticeType within {notice_id}")
        if len(fields["procedureType"]) > 1:
            errors.append(f"Inconsistent procedureType within {notice_id}")

    errors.extend(base["errors"])
    return {"errors": errors, "passed": not errors}


def write_semantic_output_csv(rows: list[OutputRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [row_to_export_dict(row) for row in rows]
    df = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    df.to_csv(path, index=False, encoding="utf-8")


def run_phase5g(
    *,
    input_path: Path = OUTPUT_CSV,
    output_path: Path = OUTPUT_CSV,
) -> Phase5gResult:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    rows = _load_rows_from_csv(input_path)
    evidence_rows = _load_pack_evidence()

    audit_rows: list[dict[str, Any]] = []
    notice_raw_types: dict[str, tuple[str, str]] = {}

    for row in rows:
        if row.notice_id not in notice_raw_types:
            notice_raw_types[row.notice_id] = (row.notice_type, row.procedure_type)

    for row in rows:
        raw_notice, raw_procedure = notice_raw_types[row.notice_id]
        ev_match = next(
            (
                ev for ev in evidence_rows
                if _evidence_key(ev.get("localFile", ""), ev.get("itemNumber", ""))
                == (row.source_document, row.item_number)
            ),
            {},
        )
        raw_supplier = str(ev_match.get("rawSupplier") or row.supplier or "")
        raw_pack = str(ev_match.get("rawPackDescription") or "")

        row.notice_type = raw_notice
        row.procedure_type = raw_procedure
        audit_rows.extend(
            apply_semantic_cleanup_to_row(
                row,
                raw_supplier=raw_supplier,
                raw_pack_description=raw_pack,
            )
        )
        _update_pack_evidence(
            evidence_rows,
            row,
            raw_notice_type=raw_notice,
            raw_procedure_type=raw_procedure,
        )

    _attach_source_rows(audit_rows, evidence_rows)

    fieldnames = list(evidence_rows[0].keys()) if evidence_rows else PACK_EVIDENCE_EXTRA_COLUMNS
    for col in PACK_EVIDENCE_EXTRA_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    with PACK_EVIDENCE_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(evidence_rows)

    with SEMANTIC_CLEANUP_AUDIT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=AUDIT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(audit_rows)

    write_semantic_output_csv(rows, output_path)
    export_text = output_path.read_text(encoding="utf-8")
    validation = validate_semantic_output(rows, export_text)
    if not validation["passed"]:
        for err in validation["errors"]:
            logger.error("Semantic validation: %s", err)
        raise ValueError(f"Semantic output validation failed: {validation['errors'][:5]}")

    output_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()

    quality = {
        "phase": "5G",
        "row_count": len(rows),
        "rows_by_molecule": dict(Counter(r.product_molecule for r in rows)),
        "audit_actions": dict(Counter(r["action"] for r in audit_rows)),
        "supplier_cleaned": [
            r for r in audit_rows if r["field"] == "supplier" and r["action"] == "blank_invalid_supplier"
        ],
        "pack_size_cleaned": [
            r for r in audit_rows if r["field"] == "packSize"
        ],
        "notice_type_mappings": {
            nid: map_notice_type(raw)[0]
            for nid, (raw, _) in notice_raw_types.items()
        },
        "procedure_type_mappings": {
            nid: map_procedure_type(raw, notice_id=nid)[0]
            for nid, (_, raw) in notice_raw_types.items()
        },
        "zero_volume_rows": [
            {"noticeId": r.notice_id, "itemNumber": r.item_number}
            for r in rows if r.packs_sold_last_12m == 0
        ],
        "output_sha256": output_sha,
        "validation": validation,
    }
    PHASE5G_QUALITY_JSON.write_text(json.dumps(quality, indent=2, default=str), encoding="utf-8")

    return Phase5gResult(
        output_path=output_path,
        audit_path=SEMANTIC_CLEANUP_AUDIT_CSV,
        evidence_path=PACK_EVIDENCE_CSV,
        quality_path=PHASE5G_QUALITY_JSON,
        row_count=len(rows),
        output_sha256=output_sha,
    )


def run_offline_build(*, seeds_root=None) -> Phase5gResult:
    from norway_tenders.settings import SEEDS_DIR
    from norway_tenders.validation.phase5b import run_phase5b
    from norway_tenders.validation.phase5e import run_phase5e

    root = seeds_root or SEEDS_DIR
    run_phase5b(seeds_root=root)
    run_phase5e(seeds_root=root)
    run_phase5f(seeds_root=root, offline=True)
    return run_phase5g()
