from __future__ import annotations

import re
from typing import Any

from norway_tenders.models import OutputRow

SPREADSHEET_ERROR_VALUES = frozenset({
    "#n/a",
    "n/a",
    "na",
    "#value!",
    "#ref!",
    "#null!",
    "null",
    "none",
    "-",
})

SUPPLIER_GROUPING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^grossister\s+vgr\s+\d+$", re.IGNORECASE),
)

NOTICE_TYPE_LABELS: dict[str, str] = {
    "3": "Contract notice",
    "cn-standard": "Competition notice",
    "v": "Voluntary ex ante transparency notice",
}

PROCEDURE_TYPE_LABELS: dict[str, str] = {
    "1": "Open procedure",
    "open": "Open procedure",
}

PROCEDURE_TYPE_NOTICE_OVERRIDES: dict[str, dict[str, str]] = {
    "682047-2022": {"v": "Award without prior publication"},
}

INTEGRAL_COUNT_FIELDS = frozenset({"packSize", "packsSoldLast12m", "estimatedValue", "awardedValue"})
PRICE_FIELDS = frozenset({"maxPrice", "awardedValue"})


def is_spreadsheet_error(value: str) -> bool:
    return (value or "").strip().casefold() in SPREADSHEET_ERROR_VALUES


def is_supplier_grouping_label(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    return any(pattern.match(text) for pattern in SUPPLIER_GROUPING_PATTERNS)


def clean_supplier_for_export(raw: str) -> tuple[str, str]:
    """Return exported supplier and rejection reason (empty if accepted)."""
    text = (raw or "").strip()
    if not text:
        return "", ""
    if is_spreadsheet_error(text):
        return "", "spreadsheet_error_placeholder"
    if is_supplier_grouping_label(text):
        return "", "supplier_grouping_label_not_legal_entity"
    return text, ""


def clean_pack_size_for_export(value: int | float | None) -> tuple[int | float | None, str]:
    """Return exportable pack size and warning code (empty if valid)."""
    if value is None:
        return None, ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None, "unparseable_pack_size"
    if numeric < 0:
        return None, "negative_pack_size"
    if numeric == 0:
        return None, "invalid_zero_pack_size"
    if numeric == int(numeric):
        return int(numeric), ""
    return numeric, ""


def map_notice_type(raw: str, *, notice_id: str = "") -> tuple[str, str]:
    """Return (readable label, warning). Raw unknown codes export as blank."""
    code = (raw or "").strip()
    if not code:
        return "", ""
    if code in NOTICE_TYPE_LABELS.values():
        return code, ""
    label = NOTICE_TYPE_LABELS.get(code.casefold())
    if label:
        return label, ""
    return "", f"unknown_notice_type:{code}"


def map_procedure_type(raw: str, *, notice_id: str = "") -> tuple[str, str]:
    """Return (readable label, warning)."""
    code = (raw or "").strip()
    if not code:
        return "", ""
    known_labels = set(PROCEDURE_TYPE_LABELS.values()) | {
        v for overrides in PROCEDURE_TYPE_NOTICE_OVERRIDES.values() for v in overrides.values()
    }
    if code in known_labels:
        return code, ""
    override = PROCEDURE_TYPE_NOTICE_OVERRIDES.get(notice_id, {}).get(code.casefold())
    if override:
        return override, ""
    label = PROCEDURE_TYPE_LABELS.get(code.casefold())
    if label:
        return label, ""
    return "", f"unknown_procedure_type:{code}"


def serialize_csv_value(field_name: str, value: Any) -> Any:
    """Format a single output field for CSV export without changing semantics."""
    if value is None or value == "":
        return ""
    if field_name in INTEGRAL_COUNT_FIELDS and isinstance(value, (int, float)):
        if value == int(value):
            return int(value)
        return value
    if field_name in PRICE_FIELDS and isinstance(value, float):
        return value
    if field_name == "packSize" and isinstance(value, (int, float)):
        if value == int(value):
            return int(value)
        return value
    return value


def row_to_export_dict(row: OutputRow) -> dict[str, Any]:
    raw = row.to_csv_dict()
    export: dict[str, Any] = {}
    for key, value in raw.items():
        export[key] = serialize_csv_value(key, value)
    if row.packs_sold_last_12m == 0:
        export["packsSoldLast12m"] = 0
    return export


def apply_semantic_cleanup_to_row(
    row: OutputRow,
    *,
    raw_supplier: str = "",
    raw_pack_description: str = "",
) -> list[dict[str, Any]]:
    """Apply export cleanup to a row; return audit entries."""
    audit: list[dict[str, Any]] = []
    supplier_source = raw_supplier or row.supplier
    cleaned_supplier, supplier_reason = clean_supplier_for_export(supplier_source)
    if supplier_source != cleaned_supplier:
        audit.append(_audit_entry(
            row, "supplier", supplier_source, cleaned_supplier,
            action="blank_invalid_supplier", reason=supplier_reason,
        ))
    row.supplier = cleaned_supplier

    cleaned_pack, pack_warning = clean_pack_size_for_export(row.pack_size)
    if row.pack_size != cleaned_pack:
        audit.append(_audit_entry(
            row, "packSize", row.pack_size, cleaned_pack,
            action="blank_invalid_pack_size", reason=pack_warning or "invalid_pack_size",
            raw_hint=raw_pack_description,
        ))
    row.pack_size = cleaned_pack

    raw_notice = row.notice_type
    readable_notice, notice_warning = map_notice_type(raw_notice, notice_id=row.notice_id)
    if raw_notice and readable_notice != raw_notice:
        audit.append(_audit_entry(
            row, "noticeType", raw_notice, readable_notice,
            action="map_notice_type", reason="",
        ))
    elif raw_notice and not readable_notice:
        audit.append(_audit_entry(
            row, "noticeType", raw_notice, "",
            action="blank_unknown_notice_type", reason=notice_warning,
        ))
    row.notice_type = readable_notice

    raw_procedure = row.procedure_type
    readable_procedure, procedure_warning = map_procedure_type(
        raw_procedure, notice_id=row.notice_id,
    )
    if raw_procedure and readable_procedure != raw_procedure:
        audit.append(_audit_entry(
            row, "procedureType", raw_procedure, readable_procedure,
            action="map_procedure_type", reason="",
        ))
    elif raw_procedure and not readable_procedure:
        audit.append(_audit_entry(
            row, "procedureType", raw_procedure, "",
            action="blank_unknown_procedure_type", reason=procedure_warning,
        ))
    row.procedure_type = readable_procedure

    return audit


def _audit_entry(
    row: OutputRow,
    field: str,
    raw_value: Any,
    cleaned_value: Any,
    *,
    action: str,
    reason: str,
    raw_hint: str = "",
) -> dict[str, Any]:
    return {
        "noticeId": row.notice_id,
        "itemNumber": row.item_number,
        "field": field,
        "rawValue": raw_hint if field == "packSize" and raw_hint else raw_value,
        "cleanedValue": cleaned_value if cleaned_value is not None else "",
        "action": action,
        "reason": reason,
        "sourceDocument": row.source_document,
        "sourceRow": "",
    }
