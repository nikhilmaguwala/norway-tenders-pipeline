from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from norway_tenders.models import MoleculeMatch, NoticeRecord, OutputRow, PackRecord
from norway_tenders.normalise.display import normalize_pack_volume, normalize_strength_display
from norway_tenders.settings import ALLOWED_MOLECULES, DETECTION_METHODS, OUTPUT_COLUMNS

logger = logging.getLogger(__name__)

COMPETITION_NOTICE_TYPES = {"cn-standard", "3", "F02", "contract notice", "competition"}
AWARD_NOTICE_TYPES = {"can-standard", "7", "F03", "contract award", "award"}


def is_competition_notice(notice_type: str) -> bool:
    nt = (notice_type or "").casefold()
    return any(token in nt for token in ("cn-standard", "contract notice", "competition", "f02")) or nt == "3"


def notice_status(notice_type: str, description: str = "") -> str:
    nt = (notice_type or "").casefold()
    desc = description.casefold()
    if "discontinued" in desc or "cancelled" in desc or "kansellert" in desc:
        return "cancelled"
    if any(t in nt for t in ("can", "award", "result")):
        return "awarded"
    if any(t in nt for t in ("cn", "competition", "contract notice")):
        return "open"
    if "pin" in nt:
        return "planned"
    return "unknown"


def merge_lifecycle_rows(rows: list[OutputRow]) -> list[OutputRow]:
    """Collapse language copies; keep lifecycle stages distinct; dedupe pack rows."""
    by_key: dict[str, OutputRow] = {}
    for row in rows:
        key = row.row_key()
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = row
            continue
        # Prefer rows with pack detail
        if row.item_number and not existing.item_number:
            by_key[key] = row
        elif row.packs_sold_last_12m is not None and existing.packs_sold_last_12m is None:
            by_key[key] = _merge_award_fields(existing, row)
        else:
            by_key[key] = _merge_award_fields(row, existing)
    return list(by_key.values())


def _merge_award_fields(base: OutputRow, award: OutputRow) -> OutputRow:
    if award.awarded_value is not None:
        base.awarded_value = award.awarded_value
    if award.awarded_supplier:
        base.awarded_supplier = award.awarded_supplier
    if award.status in {"awarded", "cancelled"}:
        base.status = award.status
    return base


def build_notice_row(notice: NoticeRecord, match: MoleculeMatch) -> OutputRow:
    return OutputRow(
        notice_id=notice.notice_id,
        tender_ref=notice.tender_ref,
        title=notice.title,
        buyer=notice.buyer,
        product_molecule=match.product_molecule,
        molecule_detected=match.molecule_detected,
        molecule_variant=match.molecule_variant,
        detection_method=match.detection_method,
        atc_code=match.atc_code,
        estimated_value=notice.estimated_value,
        currency=notice.currency or "NOK",
        notice_type=notice.notice_type,
        status=notice_status(notice.notice_type, notice.description),
        publication_date=notice.publication_date,
        contract_start=notice.contract_start,
        procedure_type=notice.procedure_type,
        source_document=f"{notice.notice_id}.xml",
        source_url=notice.source_url or notice.provenance.source_url,
    )


def build_pack_row(
    notice: NoticeRecord,
    match: MoleculeMatch,
    pack: PackRecord,
    *,
    source_document: str,
    source_url: str,
) -> OutputRow:
    row = build_notice_row(notice, match)
    row.item_number = pack.item_number
    row.product_name = pack.product_name
    raw_strength = str(pack.provenance.raw_values.get("strength", pack.strength) or "")
    row.strength = normalize_strength_display(raw_strength or pack.strength)
    row.pack_size = pack.pack_size
    row.supplier = pack.supplier
    row.max_price = pack.max_price
    row.packs_sold_last_12m = normalize_pack_volume(pack.packs_sold_last_12m)
    row.atc_code = pack.atc_code or match.atc_code
    row.source_document = source_document
    row.source_url = source_url
    return row


def build_seed_pack_row(
    pack: PackRecord,
    match: MoleculeMatch,
    *,
    tender_ref: str,
    source_document: str,
    buyer: str = "",
) -> OutputRow:
    """Build a pack row from seed documents only, without invented notice metadata."""
    raw_strength = str(pack.provenance.raw_values.get("strength", pack.strength) or "")
    return OutputRow(
        notice_id="",
        tender_ref=tender_ref,
        title="",
        buyer=buyer,
        product_molecule=match.product_molecule,
        molecule_detected=match.molecule_detected,
        molecule_variant=match.molecule_variant,
        detection_method=match.detection_method,
        atc_code=pack.atc_code or match.atc_code,
        item_number=pack.item_number,
        product_name=pack.product_name,
        strength=normalize_strength_display(raw_strength or pack.strength),
        pack_size=pack.pack_size,
        supplier=pack.supplier,
        max_price=pack.max_price,
        packs_sold_last_12m=normalize_pack_volume(pack.packs_sold_last_12m),
        currency="NOK",
        notice_type="",
        status="",
        publication_date="",
        contract_start="",
        procedure_type="",
        source_document=source_document,
        source_url="",
    )


def validate_seed_pack_rows(rows: list[OutputRow]) -> list[str]:
    """Validate seed-only pack rows do not carry invented notice metadata."""
    errors: list[str] = []
    for i, row in enumerate(rows):
        if row.notice_id:
            errors.append(f"Row {i}: noticeId must be blank for seed-only rows")
        if row.notice_id and row.tender_ref and row.notice_id == row.tender_ref.replace(" ", ""):
            errors.append(f"Row {i}: tenderRef copied into noticeId")
        if row.title:
            errors.append(f"Row {i}: title must be blank before notice enrichment")
        if row.notice_type:
            errors.append(f"Row {i}: noticeType must be blank before notice enrichment")
        if row.status:
            errors.append(f"Row {i}: status must be blank before notice enrichment")
        if row.source_url.startswith("file://"):
            errors.append(f"Row {i}: synthetic file:// sourceUrl")
        if row.source_url:
            errors.append(f"Row {i}: sourceUrl must be blank before notice enrichment")
        if row.publication_date or row.contract_start or row.procedure_type:
            errors.append(f"Row {i}: date/procedure fields must be blank before enrichment")
        if isinstance(row.pack_size, str):
            errors.append(f"Row {i}: packSize must be numeric, not text")
        if not row.source_document:
            errors.append(f"Row {i}: sourceDocument is required")
    return errors


def validate_output(rows: list[OutputRow]) -> dict[str, Any]:
    errors: list[str] = []
    keys: set[str] = set()

    for i, row in enumerate(rows):
        d = row.to_csv_dict()
        if list(d.keys()) != OUTPUT_COLUMNS:
            errors.append(f"Row {i}: column order mismatch")

        if row.product_molecule not in ALLOWED_MOLECULES:
            errors.append(f"Row {i}: molecule {row.product_molecule} not in allowlist")

        if row.detection_method and row.detection_method not in DETECTION_METHODS:
            errors.append(f"Row {i}: invalid detectionMethod {row.detection_method}")

        if row.molecule_detected and row.detection_method not in {
            "name_in_document",
            "name_in_notice",
        }:
            errors.append(f"Row {i}: moleculeDetected=true requires name-based method")

        if row.publication_date and len(row.publication_date) != 10:
            errors.append(f"Row {i}: publicationDate not ISO format")

        if not row.source_document or not row.source_url:
            errors.append(f"Row {i}: missing source provenance")

        key = row.row_key()
        if key in keys:
            errors.append(f"Row {i}: duplicate row key {key}")
        keys.add(key)

    stats = summarise_rows(rows)
    return {"errors": errors, "stats": stats, "row_count": len(rows)}


def summarise_rows(rows: list[OutputRow]) -> dict[str, Any]:
    by_molecule: dict[str, int] = {}
    by_detection: dict[str, int] = {}
    by_source: dict[str, int] = {}
    missing: dict[str, int] = {
        "maxPrice": 0,
        "packsSoldLast12m": 0,
        "estimatedValue": 0,
        "itemNumber": 0,
    }

    for row in rows:
        by_molecule[row.product_molecule] = by_molecule.get(row.product_molecule, 0) + 1
        by_detection[row.detection_method or "unknown"] = (
            by_detection.get(row.detection_method or "unknown", 0) + 1
        )
        by_source[row.source_document or "unknown"] = (
            by_source.get(row.source_document or "unknown", 0) + 1
        )
        if row.max_price is None:
            missing["maxPrice"] += 1
        if row.packs_sold_last_12m is None:
            missing["packsSoldLast12m"] += 1
        if row.estimated_value is None:
            missing["estimatedValue"] += 1
        if not row.item_number:
            missing["itemNumber"] += 1

    return {
        "by_molecule": by_molecule,
        "by_detection": by_detection,
        "by_source": by_source,
        "missing": missing,
    }


def write_output_csv(rows: list[OutputRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [row.to_csv_dict() for row in rows]
    df = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    df.to_csv(path, index=False, encoding="utf-8")
