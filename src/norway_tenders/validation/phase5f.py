from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from norway_tenders.enrichment.dmp_prices import discover_dmp_workbook, sha256_file
from norway_tenders.models import OutputRow
from norway_tenders.normalise.lifecycle import write_output_csv
from norway_tenders.parsers.lis_excel import parse_kravspec_omfang
from norway_tenders.retrieval.downloader import fetch_ted_xml
from norway_tenders.settings import OUTPUT_COLUMNS, OUTPUT_CSV, PROCESSED_DIR, SEEDS_DIR, SOURCES_SEED
from norway_tenders.validation.phase5b import validate_preview
from norway_tenders.validation.phase5e import OUTPUT_FINAL_CANDIDATE_CSV, _csv_row_to_output
from norway_tenders.validation.seed_config import PALIPERIDONE_NOTICE_TOTAL_NOK, SEED_FOLDER_META

logger = logging.getLogger(__name__)

NOTICE_VALUE_AUDIT_CSV = PROCESSED_DIR / "notice_value_audit.csv"
AWARD_METADATA_AUDIT_CSV = PROCESSED_DIR / "award_metadata_audit.csv"
NOTICE_METADATA_AUDIT_CSV = PROCESSED_DIR / "notice_metadata_audit.csv"
PHASE5F_QUALITY_JSON = PROCESSED_DIR / "phase5f_quality_report.json"

DMP_DIR_NAME = "DMP_Maximum_Prices__2026-08-03"
DMP_WORKBOOK_REL = f"{DMP_DIR_NAME}/legemiddelpriser-2026-08-03.xlsx"
DMP_EFFECTIVE_DATE = "2026-08-03"
DATASET_EXTRACTION_DATE = date(2026, 8, 14)

NOTICE_VALUE_AUDIT_COLUMNS = [
    "noticeId", "tenderRef", "productMolecule", "valueType", "rawValue", "numericValue",
    "currency", "valuePeriod", "valueScope", "sourceDocument", "sourceLocation",
    "isTargetSpecific", "mappedField", "mappingDecision", "rejectionReason",
]

AWARD_AUDIT_COLUMNS = [
    "procedureKey", "canonicalNoticeId", "awardNoticeId", "productMolecule", "awardStage",
    "awardedSupplierRaw", "awardedValueRaw", "currency", "targetScope", "linkageEvidence",
    "mappedSupplier", "mappedValue", "mappingDecision", "rejectionReason",
]

NOTICE_METADATA_AUDIT_COLUMNS = [
    "noticeId", "tenderRef", "productMolecule", "field", "currentValue", "evidenceValue",
    "sourceDocument", "sourceLocation", "mappingDecision", "rejectionReason",
]

NOTICE_LEVEL_AGGREGATION_RULE = (
    "Notice-level fields (estimatedValue, awardedValue, awardedSupplier, status) may repeat "
    "identically across pack rows for the same noticeId. Analytics aggregating contract values "
    "must deduplicate by noticeId before summing estimatedValue or awardedValue."
)

EBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
NS = {"ted": "http://publications.europa.eu/resource/schema/ted/R2.0.9/publication"}


def _iter_tag(root: ET.Element, local_name: str):
    for el in root.iter():
        if el.tag.endswith(local_name):
            yield el


@dataclass(frozen=True)
class ProcedureSpec:
    procedure_key: str
    notice_id: str
    tender_ref: str
    product_molecule: str
    seed_folder: str
    related_notice_ids: tuple[str, ...] = ()


CANONICAL_PROCEDURES: tuple[ProcedureSpec, ...] = (
    ProcedureSpec("LIS2207", "196990-2022", "2021/1727", "Axitinib", "Axitinib__LIS_2207_Oncology"),
    ProcedureSpec(
        "LIS2234", "300984-2021", "LIS 2234", "Lenalidomide", "Lenalidomide__LIS_2234",
        related_notice_ids=("335380-2021", "48506-2021", "147880-2021"),
    ),
    ProcedureSpec("2632a", "404973-2025", "2025/50837", "Everolimus", "Everolimus__2632a"),
    ProcedureSpec("2507gj-1", "244859-2024", "2507gj-1", "Anagrelide", "Anagrelide__2507gj-1"),
    ProcedureSpec(
        "LIS2301D", "682047-2022", "2022/227", "Paliperidone", "Paliperidone__LIS_2301d",
    ),
    ProcedureSpec("2601c", "434619-2026", "2601c", "Paliperidone", "Paliperidone__2601c"),
)

DMP_EXPECTED_SHA256 = "6b2fb878f149ee600940fd4361ccee276b1b2a6eb6f3a527e687bb771259216d"


@dataclass
class Phase5fResult:
    output_path: Path
    notice_value_audit_path: Path
    award_audit_path: Path
    notice_metadata_audit_path: Path
    quality_path: Path
    row_count: int
    output_sha256: str


def _iso_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if "T" in text or "+" in text or text.endswith("Z"):
        return text[:10]
    return text[:10] if len(text) >= 10 else text


def _parse_legacy_values(xml_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return rows

    for tag, field in (("VAL_ESTIMATED_TOTAL", "VAL_ESTIMATED_TOTAL"), ("VAL_TOTAL", "VAL_TOTAL")):
        for el in root.iter():
            if el.tag.endswith(tag) and el.text:
                try:
                    amount = float(el.text.replace(",", ".").strip())
                except ValueError:
                    continue
                rows.append({
                    "rawValue": el.text.strip(),
                    "numericValue": amount,
                    "currency": el.get("CURRENCY", "NOK"),
                    "sourceLocation": field,
                    "valuePeriod": "total",
                })
    return rows


def _parse_eforms_values(xml_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for el in ET.fromstring(xml_text).iter(f"{EBC}EstimatedOverallContractAmount"):
        if el.text:
            try:
                amount = float(el.text.replace(",", ".").strip())
            except ValueError:
                continue
            rows.append({
                "rawValue": el.text.strip(),
                "numericValue": amount,
                "currency": el.get("currencyID", "NOK"),
                "sourceLocation": "cbc:EstimatedOverallContractAmount",
                "valuePeriod": "total",
            })
    return rows


def _extract_values(xml_text: str) -> list[dict[str, Any]]:
    if not xml_text.strip():
        return []
    try:
        ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    legacy = _parse_legacy_values(xml_text)
    if legacy:
        return legacy
    eforms = _parse_eforms_values(xml_text)
    seen: set[tuple[str, float]] = set()
    unique: list[dict[str, Any]] = []
    for row in eforms:
        key = (row.get("sourceLocation", ""), float(row["numericValue"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _classify_value(
    proc: ProcedureSpec,
    value: dict[str, Any],
    *,
    source_document: str,
) -> dict[str, Any]:
    amount = value["numericValue"]
    base = {
        "noticeId": proc.notice_id,
        "tenderRef": proc.tender_ref,
        "productMolecule": proc.product_molecule,
        "rawValue": value["rawValue"],
        "numericValue": amount,
        "currency": value.get("currency", "NOK"),
        "valuePeriod": value.get("valuePeriod", ""),
        "sourceDocument": source_document,
        "sourceLocation": value.get("sourceLocation", ""),
        "mappedField": "",
        "mappingDecision": "rejected",
        "rejectionReason": "",
    }

    if proc.notice_id == "196990-2022" and amount >= 3_000_000_000:
        return {
            **base,
            "valueType": "umbrella_multi_molecule_value",
            "valueScope": "LIS 2207 oncology multi-molecule procurement",
            "isTargetSpecific": "false",
            "rejectionReason": "NOK 3.2bn oncology umbrella; not Axitinib-specific",
        }

    if proc.notice_id == "300984-2021" and amount >= 300_000_000:
        return {
            **base,
            "valueType": "formal_notice_estimate",
            "valueScope": "LIS 2234 dedicated Lenalidomide procurement",
            "isTargetSpecific": "true",
            "mappedField": "estimatedValue",
            "mappingDecision": "accepted",
            "rejectionReason": "",
        }

    if proc.notice_id == "404973-2025":
        return {
            **base,
            "valueType": "umbrella_multi_molecule_value",
            "valueScope": "Everolimus and mycophenolic acid combined procurement",
            "isTargetSpecific": "false",
            "rejectionReason": "Multi-molecule procedure total; no Everolimus-specific lot value",
        }

    if proc.notice_id == "244859-2024":
        return {
            **base,
            "valueType": "formal_notice_estimate",
            "valueScope": "2507gj-1 dedicated Anagrelide procurement",
            "isTargetSpecific": "true",
            "mappedField": "estimatedValue",
            "mappingDecision": "accepted",
            "rejectionReason": "",
        }

    if proc.notice_id == "682047-2022" and abs(amount - PALIPERIDONE_NOTICE_TOTAL_NOK) < 1:
        return {
            **base,
            "valueType": "umbrella_multi_molecule_value",
            "valueScope": "Seven-medicine VEAT supplementary scope",
            "isTargetSpecific": "false",
            "rejectionReason": "NOK 14,671,946 covers seven medicines; not Paliperidone-specific",
        }

    if proc.notice_id == "434619-2026":
        return {
            **base,
            "valueType": "ambiguous_value",
            "valueScope": "2601c dedicated Paliperidone procurement",
            "isTargetSpecific": "true",
            "rejectionReason": "No formal estimated contract value published in cached TED notice",
        }

    return {
        **base,
        "valueType": "ambiguous_value",
        "valueScope": proc.tender_ref,
        "isTargetSpecific": "unknown",
        "rejectionReason": "Unclassified value for procedure",
    }


def _kravspec_turnover_rows(proc: ProcedureSpec, seeds_root: Path) -> list[dict[str, Any]]:
    if proc.seed_folder not in SEED_FOLDER_META:
        return []
    folder = seeds_root / proc.seed_folder
    rows: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.xlsx")):
        if "krav" not in path.name.casefold():
            continue
        evidence = parse_kravspec_omfang(path)
        if not evidence or evidence.get("historical_turnover_aip") is None:
            continue
        amount = float(evidence["historical_turnover_aip"])
        rows.append({
            "noticeId": proc.notice_id,
            "tenderRef": proc.tender_ref,
            "productMolecule": proc.product_molecule,
            "valueType": "historical_max_aip_turnover",
            "rawValue": str(amount),
            "numericValue": amount,
            "currency": "NOK",
            "valuePeriod": "historical",
            "valueScope": f"{proc.product_molecule} kravspesifikasjon omfang",
            "sourceDocument": path.name,
            "sourceLocation": "Kravspesifikasjon omfang / historical turnover",
            "isTargetSpecific": "true",
            "mappedField": "",
            "mappingDecision": "rejected",
            "rejectionReason": "Historical max-AIP turnover; not a formal notice contract estimate",
        })
    return rows


def build_notice_value_audit(*, seeds_root: Path = SEEDS_DIR, offline: bool = True) -> list[dict[str, Any]]:
    audit_rows: list[dict[str, Any]] = []
    for proc in CANONICAL_PROCEDURES:
        xml = fetch_ted_xml(proc.notice_id, offline=offline)
        values = _extract_values(xml)
        if not values and proc.notice_id == "434619-2026":
            audit_rows.append({
                "noticeId": proc.notice_id,
                "tenderRef": proc.tender_ref,
                "productMolecule": proc.product_molecule,
                "valueType": "confidential_or_unpublished",
                "rawValue": "",
                "numericValue": "",
                "currency": "NOK",
                "valuePeriod": "",
                "valueScope": "2601c dedicated Paliperidone procurement",
                "sourceDocument": f"{proc.notice_id}.xml",
                "sourceLocation": "EstimatedOverallContractAmount",
                "isTargetSpecific": "true",
                "mappedField": "",
                "mappingDecision": "rejected",
                "rejectionReason": "No published estimated contract value in cached TED XML",
            })
        for value in values:
            row = _classify_value(proc, value, source_document=f"{proc.notice_id}.xml")
            audit_rows.append(row)
        audit_rows.extend(_kravspec_turnover_rows(proc, seeds_root))
    return audit_rows


def _parse_award_notice(xml_text: str) -> dict[str, Any]:
    result = {
        "awarded_supplier": "",
        "awarded_value": None,
        "currency": "NOK",
        "no_award": False,
        "award_stage": "award_notice",
    }
    if "NO_AWARDED_CONTRACT" in xml_text or "NO_AWARD" in xml_text:
        result["no_award"] = True
        result["award_stage"] = "no_award_published"
    for el in ET.fromstring(xml_text).iter():
        if el.tag.endswith("VAL_TOTAL") and el.text:
            try:
                result["awarded_value"] = float(el.text.replace(",", ".").strip())
                result["currency"] = el.get("CURRENCY", "NOK")
            except ValueError:
                pass
    if "Nordic Pill" in xml_text:
        match = re.search(r"Nordic Pill AB", xml_text)
        if match:
            result["awarded_supplier"] = match.group(0)
            result["award_stage"] = "prospective_veat_supplier"
    return result


def build_award_metadata_audit(*, offline: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in CANONICAL_PROCEDURES:
        related = list(proc.related_notice_ids) + [proc.notice_id]
        for notice_id in related:
            xml = fetch_ted_xml(notice_id, offline=offline)
            parsed = _parse_award_notice(xml)
            base = {
                "procedureKey": proc.procedure_key,
                "canonicalNoticeId": proc.notice_id,
                "awardNoticeId": notice_id,
                "productMolecule": proc.product_molecule,
                "awardStage": parsed["award_stage"],
                "awardedSupplierRaw": parsed["awarded_supplier"],
                "awardedValueRaw": parsed["awarded_value"] if parsed["awarded_value"] is not None else "",
                "currency": parsed["currency"],
                "targetScope": proc.tender_ref,
                "linkageEvidence": "canonical procedure" if notice_id == proc.notice_id else "lifecycle related notice",
                "mappedSupplier": "",
                "mappedValue": "",
                "mappingDecision": "rejected",
                "rejectionReason": "",
            }

            if notice_id == "335380-2021":
                base["linkageEvidence"] = "Phase 4B lifecycle audit; LIS 2234 award notice"
                base["rejectionReason"] = "Award notice contains NO_AWARDED_CONTRACT; no concluded winner/value"
            elif notice_id == "682047-2022":
                base["awardStage"] = "prospective_veat_supplier"
                base["rejectionReason"] = (
                    "Prospective VEAT multi-lot supplier mention; insufficient for concluded Paliperidone award"
                )
            elif parsed["no_award"]:
                base["rejectionReason"] = "No awarded contract published in notice"
            elif parsed["awarded_supplier"] or parsed["awarded_value"] is not None:
                base["rejectionReason"] = "Award evidence not target-specific or not concluded"
            else:
                base["rejectionReason"] = "No award supplier or value in cached notice"

            rows.append(base)
    return rows


def _extract_notice_metadata(xml_text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return meta

    td = root.find(".//ted:TD_DOCUMENT_TYPE", NS)
    if td is not None and td.get("CODE"):
        meta["noticeType"] = td.get("CODE", "")
    for el in root.iter(f"{EBC}NoticeTypeCode"):
        if el.text:
            meta["noticeType"] = el.text.strip()
    for el in _iter_tag(root, "NoticeTypeCode"):
        if el.text and not meta.get("noticeType"):
            meta["noticeType"] = el.text.strip()

    pr = root.find(".//ted:PR_PROC", NS)
    if pr is not None and pr.get("CODE"):
        meta["procedureType"] = pr.get("CODE", "")
    for el in root.iter(f"{EBC}ProcedureCode"):
        if el.text:
            meta["procedureType"] = el.text.strip()
    for el in _iter_tag(root, "ProcedureCode"):
        if el.text and not meta.get("procedureType"):
            meta["procedureType"] = el.text.strip()

    for el in root.iter(f"{EBC}PublicationDate"):
        if el.text:
            meta["publicationDate"] = _iso_date(el.text)
            break
    if not meta.get("publicationDate"):
        for el in _iter_tag(root, "PublicationDate"):
            if el.text:
                meta["publicationDate"] = _iso_date(el.text)
                break
    if not meta.get("publicationDate"):
        for el in root.iter():
            if el.tag.endswith("DATE_PUB") and el.text:
                meta["publicationDate"] = _iso_date(el.text)
                break
            if el.tag.endswith("DS_DATE_DISPATCH") and el.text:
                meta["publicationDate"] = _iso_date(el.text)
                break

    date_start = root.find(".//ted:DATE_START", NS)
    if date_start is not None and date_start.text:
        meta["contractStart"] = _iso_date(date_start.text)
    for el in root.iter(f"{EBC}StartDate"):
        if el.text and not meta.get("contractStart"):
            meta["contractStart"] = _iso_date(el.text)
    for el in _iter_tag(root, "StartDate"):
        if el.text and not meta.get("contractStart"):
            meta["contractStart"] = _iso_date(el.text)

    return meta


def _derive_status(notice_id: str, notice_type: str, xml_text: str) -> str:
    if notice_id != "434619-2026":
        return ""
    if notice_type != "cn-standard":
        return ""
    deadline = ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    for el in _iter_tag(root, "EndDate"):
        if el.text:
            deadline = _iso_date(el.text)
            break
    if deadline and DATASET_EXTRACTION_DATE <= date.fromisoformat(deadline):
        return "open"
    return ""


def build_notice_metadata_audit(
    *,
    current_by_notice: dict[str, dict[str, str]] | None = None,
    offline: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_by_notice = current_by_notice or {}
    for proc in CANONICAL_PROCEDURES:
        xml = fetch_ted_xml(proc.notice_id, offline=offline)
        evidence = _extract_notice_metadata(xml)
        status_value = _derive_status(proc.notice_id, evidence.get("noticeType", ""), xml)
        if status_value:
            evidence["status"] = status_value

        current = current_by_notice.get(proc.notice_id, {})
        for field in ("noticeType", "status", "publicationDate", "contractStart", "procedureType"):
            evidence_value = evidence.get(field, "")
            current_value = current.get(field, "")
            if evidence_value:
                decision = "accepted"
                reason = ""
            else:
                decision = "blank"
                reason = "No explicit evidence or conservative blank policy"
            rows.append({
                "noticeId": proc.notice_id,
                "tenderRef": proc.tender_ref,
                "productMolecule": proc.product_molecule,
                "field": field,
                "currentValue": current_value,
                "evidenceValue": evidence_value,
                "sourceDocument": f"{proc.notice_id}.xml",
                "sourceLocation": field,
                "mappingDecision": decision,
                "rejectionReason": reason,
            })
    return rows


def _accepted_estimates(audit_rows: list[dict[str, Any]]) -> dict[str, float]:
    accepted: dict[str, float] = {}
    for row in audit_rows:
        if row.get("mappingDecision") == "accepted" and row.get("mappedField") == "estimatedValue":
            notice_id = row["noticeId"]
            accepted[notice_id] = float(row["numericValue"])
    return accepted


def _metadata_by_notice(audit_rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    by_notice: dict[str, dict[str, str]] = defaultdict(dict)
    for row in audit_rows:
        if row.get("mappingDecision") == "accepted" and row.get("evidenceValue"):
            by_notice[row["noticeId"]][row["field"]] = row["evidenceValue"]
    return dict(by_notice)


def _current_metadata_by_notice(rows: list[OutputRow]) -> dict[str, dict[str, str]]:
    by_notice: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.notice_id not in by_notice:
            by_notice[row.notice_id] = {
                "noticeType": row.notice_type or "",
                "status": row.status or "",
                "publicationDate": row.publication_date or "",
                "contractStart": row.contract_start or "",
                "procedureType": row.procedure_type or "",
            }
    return by_notice


def apply_metadata(
    rows: list[OutputRow],
    *,
    value_audit: list[dict[str, Any]],
    metadata_audit: list[dict[str, Any]],
) -> list[OutputRow]:
    estimates = _accepted_estimates(value_audit)
    meta = _metadata_by_notice(metadata_audit)

    for row in rows:
        notice_meta = meta.get(row.notice_id, {})
        for field, attr in (
            ("noticeType", "notice_type"),
            ("status", "status"),
            ("publicationDate", "publication_date"),
            ("contractStart", "contract_start"),
            ("procedureType", "procedure_type"),
        ):
            value = notice_meta.get(field)
            if value is not None:
                setattr(row, attr, value)

        if row.notice_id in estimates:
            row.estimated_value = estimates[row.notice_id]

        row.awarded_value = None
        row.awarded_supplier = ""

    return rows


def deduplicate_notice_metrics(rows: list[OutputRow], field: str = "estimatedValue") -> dict[str, float]:
    """Sum notice-level metrics after deduplicating by noticeId (for analytics)."""
    attr = "estimated_value" if field == "estimatedValue" else "awarded_value"
    totals: dict[str, float] = {}
    for row in rows:
        value = getattr(row, attr)
        if value is None:
            continue
        totals[row.notice_id] = float(value)
    return totals


def sum_deduplicated_notice_field(rows: list[OutputRow], field: str = "estimatedValue") -> float:
    return sum(deduplicate_notice_metrics(rows, field).values())


def correct_dmp_sources_path(*, seeds_root: Path = SEEDS_DIR) -> str:
    workbook = seeds_root / DMP_WORKBOOK_REL
    if not workbook.exists():
        raise FileNotFoundError(f"DMP workbook not found at {workbook}")
    digest = sha256_file(workbook)
    if digest != DMP_EXPECTED_SHA256:
        raise ValueError(f"DMP workbook SHA-256 changed: {digest}")

    if not SOURCES_SEED.exists():
        return digest

    with SOURCES_SEED.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        source_rows = [dict(row) for row in reader]

    for row in source_rows:
        if row.get("sourceType") == "official_maximum_price_reference":
            row["localFile"] = DMP_WORKBOOK_REL
            row["effectiveDate"] = DMP_EFFECTIVE_DATE
            row["sha256"] = digest

    with SOURCES_SEED.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(source_rows)
    return digest


def validate_final_output(rows: list[OutputRow]) -> dict[str, Any]:
    validation = validate_preview(rows)
    errors = list(validation["errors"])

    if len(rows) != 41:
        errors.append(f"Expected 41 rows, got {len(rows)}")

    by_notice_est: dict[str, set[float | None]] = defaultdict(set)
    for row in rows:
        by_notice_est[row.notice_id].add(row.estimated_value)

    for notice_id, values in by_notice_est.items():
        if len(values) > 1:
            errors.append(f"estimatedValue inconsistent within noticeId {notice_id}")

    for row in rows:
        if row.estimated_value and row.estimated_value >= 3_000_000_000:
            errors.append(f"Umbrella estimatedValue on {row.notice_id}")
        if row.awarded_value == PALIPERIDONE_NOTICE_TOTAL_NOK:
            errors.append(f"Paliperidone umbrella award value on {row.notice_id}")

    molecules = Counter(row.product_molecule for row in rows)
    if set(molecules) != {"Axitinib", "Everolimus", "Lenalidomide", "Anagrelide", "Paliperidone"}:
        errors.append(f"Unexpected molecules: {dict(molecules)}")

    return {"errors": errors, "passed": not errors, "stats": validation.get("stats", {})}


def _write_audit_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_phase5f(
    *,
    seeds_root: Path = SEEDS_DIR,
    candidate_path: Path = OUTPUT_FINAL_CANDIDATE_CSV,
    output_path: Path = OUTPUT_CSV,
    offline: bool = True,
) -> Phase5fResult:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dmp_sha = correct_dmp_sources_path(seeds_root=seeds_root)
    discover_dmp_workbook(seeds_root)

    candidate_rows = [
        _csv_row_to_output(row)
        for row in csv.DictReader(candidate_path.open(encoding="utf-8"))
    ]

    value_audit = build_notice_value_audit(seeds_root=seeds_root, offline=offline)
    award_audit = build_award_metadata_audit(offline=offline)
    metadata_audit = build_notice_metadata_audit(
        current_by_notice=_current_metadata_by_notice(candidate_rows),
        offline=offline,
    )

    _write_audit_csv(NOTICE_VALUE_AUDIT_CSV, NOTICE_VALUE_AUDIT_COLUMNS, value_audit)
    _write_audit_csv(AWARD_METADATA_AUDIT_CSV, AWARD_AUDIT_COLUMNS, award_audit)
    _write_audit_csv(NOTICE_METADATA_AUDIT_CSV, NOTICE_METADATA_AUDIT_COLUMNS, metadata_audit)

    final_rows = apply_metadata(
        candidate_rows,
        value_audit=value_audit,
        metadata_audit=metadata_audit,
    )

    validation = validate_final_output(final_rows)
    if not validation["passed"]:
        for err in validation["errors"]:
            logger.error("Final validation: %s", err)
        raise ValueError(f"Final output validation failed: {validation['errors'][:5]}")

    write_output_csv(final_rows, output_path)
    output_sha = _file_sha256(output_path)

    missing = {
        "estimatedValue": sum(1 for r in final_rows if r.estimated_value is None),
        "awardedValue": sum(1 for r in final_rows if r.awarded_value is None),
        "awardedSupplier": sum(1 for r in final_rows if not r.awarded_supplier),
    }
    missing_pct = {k: round(100 * v / len(final_rows), 1) for k, v in missing.items()}

    quality = {
        "phase": "5F",
        "dmp_directory_correction": {
            "directory": DMP_DIR_NAME,
            "localFile": DMP_WORKBOOK_REL,
            "effectiveDate": DMP_EFFECTIVE_DATE,
            "sha256": dmp_sha,
            "sha256_unchanged": dmp_sha == DMP_EXPECTED_SHA256,
        },
        "notice_level_aggregation_rule": NOTICE_LEVEL_AGGREGATION_RULE,
        "accepted_estimates_by_notice": {
            nid: val for nid, val in _accepted_estimates(value_audit).items()
        },
        "row_count": len(final_rows),
        "rows_by_molecule": dict(Counter(r.product_molecule for r in final_rows)),
        "missingness": missing,
        "missingness_percent": missing_pct,
        "output_sha256": output_sha,
        "validation": validation,
    }
    PHASE5F_QUALITY_JSON.write_text(json.dumps(quality, indent=2, default=str), encoding="utf-8")

    return Phase5fResult(
        output_path=output_path,
        notice_value_audit_path=NOTICE_VALUE_AUDIT_CSV,
        award_audit_path=AWARD_METADATA_AUDIT_CSV,
        notice_metadata_audit_path=NOTICE_METADATA_AUDIT_CSV,
        quality_path=PHASE5F_QUALITY_JSON,
        row_count=len(final_rows),
        output_sha256=output_sha,
    )


def run_offline_build(*, seeds_root: Path = SEEDS_DIR) -> Phase5fResult:
    from norway_tenders.validation.phase5g import run_offline_build as run_full_build

    result = run_full_build(seeds_root=seeds_root)
    return Phase5fResult(
        output_path=result.output_path,
        notice_value_audit_path=NOTICE_VALUE_AUDIT_CSV,
        award_audit_path=AWARD_METADATA_AUDIT_CSV,
        notice_metadata_audit_path=NOTICE_METADATA_AUDIT_CSV,
        quality_path=result.quality_path,
        row_count=result.row_count,
        output_sha256=result.output_sha256,
    )
