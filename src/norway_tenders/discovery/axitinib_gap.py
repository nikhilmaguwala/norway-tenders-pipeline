from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from norway_tenders.discovery.candidates import (
    _enrich_from_cached_xml,
    _lifecycle_stage,
    _parse_notice,
)
from norway_tenders.discovery.ted_search import execute_queries
from norway_tenders.matching.matcher import load_molecule_config, match_evidence
from norway_tenders.models import NoticeRecord
from norway_tenders.settings import AXITINIB_GAP_REPORT, CACHE_DIR

logger = logging.getLogger(__name__)

AXITINIB_QUERY_TERMS: list[tuple[str, str]] = [
    ("name:axitinib", 'FT~"axitinib"'),
    ("brand:Inlyta", 'FT~"Inlyta"'),
    ("atc:L01EK01", 'FT~"L01EK01"'),
    ("atc:L01XE17", 'FT~"L01XE17"'),
    ("area:urologisk kreft", 'FT~"urologisk kreft"'),
    ("area:nyrekreft", 'FT~"nyrekreft"'),
    ("area:renal cancer", 'FT~"renal cancer"'),
    ("area:renal cell carcinoma", 'FT~"renal cell carcinoma"'),
    ("area:onkologi", 'FT~"onkologi"'),
    ("area:oncology", 'FT~"oncology"'),
    ("doc:virkestoffliste", 'FT~"virkestoffliste"'),
    ("tender:LIS 2007h", 'FT~"LIS 2007h"'),
    ("tender:LIS 2107b", 'FT~"LIS 2107b"'),
    ("buyer+area:Sykehusinnkjøp+urologisk kreft", 'FT~"Sykehusinnkjøp" AND FT~"urologisk kreft"'),
    ("buyer+area:Sykehusinnkjøp+oncology", 'FT~"Sykehusinnkjøp" AND FT~"oncology"'),
    ("buyer+atc:Sykehusinnkjøp+L01EK01", 'FT~"Sykehusinnkjøp" AND FT~"L01EK01"'),
    ("buyer+atc:Sykehusinnkjøp+L01XE17", 'FT~"Sykehusinnkjøp" AND FT~"L01XE17"'),
    ("doc:Prisskjema", 'FT~"Prisskjema" AND FT~"onkologi"'),
    ("doc:Kravspesifikasjon", 'FT~"Kravspesifikasjon" AND FT~"onkologi"'),
    ("doc:Vedlegg 02", 'FT~"Vedlegg 02" AND FT~"Sykehusinnkjøp"'),
    ("doc:Vedlegg 03", 'FT~"Vedlegg 03" AND FT~"Sykehusinnkjøp"'),
]

DOCUMENT_URL_PATTERNS = re.compile(
    r"(prisskjema|kravspesifikasjon|virkestoffliste|vedlegg\s*0[23]|oncology|onkologi|urologisk)",
    re.I,
)

AXITINIB_NAME = re.compile(r"\baxitinib\b", re.I)
AXITINIB_ATC = re.compile(r"\bL01EK01\b|\bL01XE17\b", re.I)
AXITINIB_BRAND = re.compile(r"\bInlyta\b", re.I)
THERAPEUTIC_AREA = re.compile(
    r"\b(urologisk\s+kreft|nyrekreft|renal\s+cell|oncology|onkologi)\b",
    re.I,
)
LIS_ONCOLOGY = re.compile(r"\bLIS\s*(2007h|2107b|2107)\b", re.I)

GAP_COLUMNS = [
    "queryUsed",
    "noticeId",
    "tenderRef",
    "title",
    "buyer",
    "publicationDate",
    "lifecycleStage",
    "noticeUrl",
    "documentUrl",
    "matchedEvidence",
    "evidenceLocation",
    "proposedDecision",
    "reason",
]


@dataclass
class AxitinibGapResult:
    rows: list[dict[str, Any]]
    queries_executed: int
    notice_ids: set[str]
    tender_families: set[str]
    confirmation: str
    errors: list[str]


def _extract_document_urls(notice: NoticeRecord, raw: dict[str, Any], xml_text: str = "") -> list[str]:
    urls: list[str] = []
    blob = xml_text or notice.description
    for pattern in (
        r"https?://[^\s\"'<>]*mercell[^\s\"'<>]*",
        r"https?://[^\s\"'<>]*permalink[^\s\"'<>]*",
    ):
        for match in re.finditer(pattern, blob, re.I):
            urls.append(match.group(0).rstrip(".,)"))
    if notice.source_url:
        urls.append(notice.source_url)
    links = raw.get("links", {})
    if isinstance(links, dict):
        html = links.get("html", {})
        if isinstance(html, dict):
            eng = html.get("ENG") or html.get("NOR")
            if eng:
                urls.append(str(eng))
    return list(dict.fromkeys(urls))


def _evaluate_axitinib_evidence(
    notice: NoticeRecord,
    xml_text: str,
    query_label: str,
) -> tuple[str, str, str, str]:
    """Return matchedEvidence, evidenceLocation, proposedDecision, reason."""
    notice_text = f"{notice.title} {notice.description}"
    doc_text = xml_text or ""
    combined = f"{notice_text} {doc_text}"

    name_hit = AXITINIB_NAME.search(combined)
    atc_hit = AXITINIB_ATC.search(combined)
    brand_hit = AXITINIB_BRAND.search(combined)
    area_hit = THERAPEUTIC_AREA.search(combined) or LIS_ONCOLOGY.search(combined)

    if name_hit:
        loc = "notice_xml" if name_hit.group(0) in doc_text else "notice_title_description"
        return name_hit.group(0), loc, "needs_review", "Axitinib name found; confirm in tender documents"
    if atc_hit:
        loc = "notice_xml" if atc_hit.group(0) in doc_text else "notice_title_description"
        return atc_hit.group(0), loc, "needs_review", "Axitinib ATC found; confirm pack rows in documents"
    if brand_hit and not name_hit:
        loc = "notice_xml" if brand_hit.group(0) in doc_text else "notice_title_description"
        return brand_hit.group(0), loc, "needs_review", "Inlyta brand only; requires molecule/ATC document confirmation"
    if area_hit or query_label.startswith(("area:", "doc:", "tender:", "buyer+area")):
        term = area_hit.group(0) if area_hit else query_label.split(":", 1)[-1]
        return term, "notice_query_match", "needs_review", "Broad oncology/urology candidate; Axitinib not in notice title"
    if DOCUMENT_URL_PATTERNS.search(combined):
        return "procurement_document_ref", "notice_xml", "needs_review", "Document reference found; Axitinib confirmation pending"
    return "", "", "needs_review", "TED query hit without direct Axitinib evidence in notice"


def _scan_cached_xml_for_axitinib() -> list[dict[str, Any]]:
    """Scan locally cached TED XML for Axitinib and oncology document evidence."""
    rows: list[dict[str, Any]] = []
    xml_dir = CACHE_DIR / "ted_xml"
    if not xml_dir.exists():
        return rows

    for xml_path in xml_dir.glob("*.xml"):
        xml_text = xml_path.read_text(encoding="utf-8", errors="ignore")
        if not (
            AXITINIB_NAME.search(xml_text)
            or AXITINIB_ATC.search(xml_text)
            or AXITINIB_BRAND.search(xml_text)
            or LIS_ONCOLOGY.search(xml_text)
            or (THERAPEUTIC_AREA.search(xml_text) and DOCUMENT_URL_PATTERNS.search(xml_text))
        ):
            continue

        notice_id = xml_path.stem
        notice = NoticeRecord(notice_id=notice_id)
        notice = _enrich_from_cached_xml(notice)
        evidence, location, decision, reason = _evaluate_axitinib_evidence(
            notice, xml_text, "cached_xml_scan"
        )
        doc_urls = _extract_document_urls(notice, {}, xml_text)
        rows.append(
            {
                "queryUsed": "cached_xml_scan",
                "noticeId": notice_id,
                "tenderRef": notice.tender_ref,
                "title": notice.title,
                "buyer": notice.buyer,
                "publicationDate": notice.publication_date,
                "lifecycleStage": _lifecycle_stage(notice.notice_type),
                "noticeUrl": notice.source_url or f"https://ted.europa.eu/en/notice/-/detail/{notice_id}",
                "documentUrl": doc_urls[0] if doc_urls else "",
                "matchedEvidence": evidence,
                "evidenceLocation": location,
                "proposedDecision": decision,
                "reason": reason,
            }
        )
    return rows


def run_axitinib_gap_search(*, offline: bool = False, refresh: bool = False) -> AxitinibGapResult:
    config = load_molecule_config()["Axitinib"]
    assert "inlyta" in [b.lower() for b in config.discovery_brands]

    search_results, errors = execute_queries(AXITINIB_QUERY_TERMS, offline=offline, refresh=refresh)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for query_label, notices in search_results.items():
        for raw in notices:
            notice = _parse_notice(raw)
            if not notice.notice_id:
                continue
            notice = _enrich_from_cached_xml(notice)
            xml_path = CACHE_DIR / "ted_xml" / f"{notice.notice_id}.xml"
            xml_text = xml_path.read_text(encoding="utf-8", errors="ignore") if xml_path.exists() else ""

            key = (notice.notice_id, query_label)
            if key in seen:
                continue
            seen.add(key)

            evidence, location, decision, reason = _evaluate_axitinib_evidence(
                notice, xml_text, query_label
            )
            doc_urls = _extract_document_urls(notice, raw, xml_text)
            rows.append(
                {
                    "queryUsed": query_label,
                    "noticeId": notice.notice_id,
                    "tenderRef": notice.tender_ref,
                    "title": notice.title,
                    "buyer": notice.buyer,
                    "publicationDate": notice.publication_date,
                    "lifecycleStage": _lifecycle_stage(notice.notice_type),
                    "noticeUrl": notice.source_url,
                    "documentUrl": ";".join(doc_urls[:3]),
                    "matchedEvidence": evidence,
                    "evidenceLocation": location,
                    "proposedDecision": decision,
                    "reason": reason,
                }
            )

    for cached_row in _scan_cached_xml_for_axitinib():
        key = (cached_row["noticeId"], cached_row["queryUsed"])
        if key not in seen:
            seen.add(key)
            rows.append(cached_row)

    notice_ids = {r["noticeId"] for r in rows}
    families = set()
    for r in rows:
        m = re.search(r"LIS\s*\d+[a-z]?", f"{r['tenderRef']} {r['title']}", re.I)
        if m:
            families.add(m.group(0).upper().replace(" ", ""))
        elif r["tenderRef"]:
            families.add(r["tenderRef"])

    has_name = any(AXITINIB_NAME.search(r["matchedEvidence"] or "") for r in rows)
    has_atc = any(AXITINIB_ATC.search(r["matchedEvidence"] or "") for r in rows)
    has_brand = any(r["matchedEvidence"] == "Inlyta" or AXITINIB_BRAND.search(r["matchedEvidence"] or "") for r in rows)
    if has_name:
        confirmation = "confirmed_by_name"
    elif has_atc:
        confirmation = "confirmed_by_atc"
    elif has_brand:
        confirmation = "brand_only_document_dependent"
    else:
        confirmation = "document_dependent_no_notice_confirmation"

    AXITINIB_GAP_REPORT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=GAP_COLUMNS).to_csv(AXITINIB_GAP_REPORT, index=False, encoding="utf-8")

    return AxitinibGapResult(
        rows=rows,
        queries_executed=len(AXITINIB_QUERY_TERMS),
        notice_ids=notice_ids,
        tender_families=families,
        confirmation=confirmation,
        errors=errors,
    )
