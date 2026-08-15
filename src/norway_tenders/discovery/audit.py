from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from norway_tenders.discovery.candidates import _enrich_from_cached_xml, _lifecycle_stage
from norway_tenders.matching.evidence import EXAMPLE_CONTEXT, PHARMA_BUYERS
from norway_tenders.matching.matcher import load_molecule_config, match_evidence
from norway_tenders.models import MoleculeMatch, NoticeRecord
from norway_tenders.settings import (
    AUDITED_CANDIDATES_CSV,
    CACHE_DIR,
    PROCEDURE_SUMMARY_CSV,
    REVIEW_CANDIDATES_CSV,
)

logger = logging.getLogger(__name__)

EVIDENCE_LEVELS = (
    "document_name",
    "document_atc",
    "notice_name",
    "notice_atc",
    "brand_only",
    "therapeutic_area_only",
    "broad_candidate",
)

BRAND_PATTERN = re.compile(
    r"\b(revlimid|inlyta|afinitor|certican|zortress|agrylin|xeplion|invega|trevicta)\b",
    re.I,
)
THERAPEUTIC_AREA = re.compile(
    r"\b(oncology|onkologi|urologisk|pharmaceutical products|legemiddel)\b",
    re.I,
)
LIS_REF = re.compile(r"\bLIS\s*(\d+[a-z]?)\b", re.I)

AUDIT_COLUMNS = [
    "targetMolecule",
    "queryUsed",
    "noticeId",
    "tenderRef",
    "title",
    "buyer",
    "publicationDate",
    "lifecycleStage",
    "noticeUrl",
    "documentUrls",
    "procedureKey",
    "evidenceLevel",
    "exactMatchedText",
    "evidenceLocation",
    "hasDirectMoleculeName",
    "hasDirectAtc",
    "hasDocumentLinks",
    "likelyLanguageDuplicate",
    "likelyLifecycleRelated",
    "canonicalProcedureCandidate",
    "requiresDocumentConfirmation",
    "originalDecision",
    "auditedDecision",
    "auditReason",
    "possibleDuplicateOf",
]

PROCEDURE_COLUMNS = [
    "targetMolecule",
    "procedureKey",
    "noticeCount",
    "lifecycleStages",
    "noticeIds",
    "acceptedEvidenceLevel",
    "hasDocumentLinks",
    "canonicalPackBearingNotice",
    "awardNoticeId",
    "recommendedFetchPriority",
]


@dataclass
class AuditResult:
    audited_rows: list[dict[str, Any]] = field(default_factory=list)
    procedure_rows: list[dict[str, Any]] = field(default_factory=list)
    before_counts: dict[str, int] = field(default_factory=dict)
    after_counts: dict[str, int] = field(default_factory=dict)
    downgrades: list[dict[str, Any]] = field(default_factory=list)
    language_duplicate_count: int = 0
    lifecycle_related_count: int = 0
    evidence_level_counts: dict[str, int] = field(default_factory=dict)
    procedures_by_molecule: dict[str, int] = field(default_factory=dict)


def procedure_key(tender_ref: str, title: str, notice_id: str) -> str:
    blob = f"{tender_ref} {title}"
    match = LIS_REF.search(blob)
    if match:
        return f"LIS{match.group(1).upper().replace(' ', '')}"
    ref = (tender_ref or "").strip().upper().replace(" ", "")
    if ref:
        return ref
    return notice_id


def _notice_text(notice: NoticeRecord, xml_text: str) -> tuple[str, str]:
    notice_part = f"{notice.title} {notice.description}".strip()
    return notice_part, xml_text


def _classify_evidence(
    target_molecule: str,
    notice_text: str,
    xml_text: str,
    match: MoleculeMatch | None,
) -> tuple[str, str, str, bool, bool]:
    config = load_molecule_config()
    spec = config.get(target_molecule)
    names = list(spec.names) if spec else []
    atcs = list(spec.atc_codes) if spec else []
    brands = list(spec.discovery_brands) if spec else []

    doc_match = match_evidence(xml_text, context="document") if xml_text else None
    notice_match = match or (match_evidence(notice_text, context="notice") if notice_text else None)

    has_name_notice = any(re.search(rf"\b{re.escape(n)}\b", notice_text, re.I) for n in names)
    has_atc_notice = any(re.search(rf"\b{re.escape(a)}\b", notice_text, re.I) for a in atcs)
    has_name_doc = bool(doc_match and doc_match.molecule_detected and doc_match.product_molecule == target_molecule)
    has_atc_doc = bool(
        doc_match
        and doc_match.product_molecule == target_molecule
        and doc_match.atc_code
        and not doc_match.molecule_detected
    )

    brand_hit = BRAND_PATTERN.search(notice_text) or BRAND_PATTERN.search(xml_text)
    brand_only = bool(
        brand_hit
        and not has_name_notice
        and not has_atc_notice
        and not has_name_doc
        and not has_atc_doc
    )

    if has_name_doc:
        return (
            "document_name",
            doc_match.molecule_variant if doc_match else "",
            "document_xml",
            True,
            bool(doc_match and doc_match.atc_code),
        )
    if has_atc_doc:
        return (
            "document_atc",
            doc_match.atc_code if doc_match else "",
            "document_xml",
            False,
            True,
        )
    if has_name_notice and notice_match and notice_match.molecule_detected:
        return (
            "notice_name",
            notice_match.molecule_variant or "",
            "notice_title_description",
            True,
            bool(notice_match.atc_code),
        )
    if has_atc_notice or (notice_match and notice_match.detection_method.startswith("atc")):
        atc = notice_match.atc_code if notice_match else ""
        return ("notice_atc", atc, "notice_title_description", False, True)
    if brand_only:
        return ("brand_only", brand_hit.group(0) if brand_hit else "", "notice_or_xml", False, False)
    if THERAPEUTIC_AREA.search(notice_text) or THERAPEUTIC_AREA.search(xml_text):
        return ("therapeutic_area_only", "", "notice_or_xml", False, False)
    return ("broad_candidate", "", "notice_query", False, False)


def _audited_decision(
    evidence_level: str,
    original: str,
    notice: NoticeRecord,
    target_molecule: str,
) -> tuple[str, str]:
    if evidence_level in {"document_name", "document_atc", "notice_name", "notice_atc"}:
        text = f"{notice.title} {notice.description}"
        if EXAMPLE_CONTEXT.search(text) and notice.buyer.casefold() not in PHARMA_BUYERS:
            return "needs_review", "Molecule in illustrative example context"
        if evidence_level in {"notice_name", "notice_atc"} and notice.buyer.casefold() in PHARMA_BUYERS:
            return "accepted", f"Retained: {evidence_level}"
        if evidence_level.startswith("document_"):
            return "accepted", f"Retained: {evidence_level}"
        if notice.buyer.casefold() in PHARMA_BUYERS:
            return "accepted", f"Retained: {evidence_level} with Sykehusinnkjøp buyer"
        return "needs_review", f"{evidence_level} but non-Sykehusinnkjøp buyer"

    if evidence_level == "brand_only":
        return "needs_review", "Brand-only evidence; molecule/ATC confirmation required"
    if evidence_level in {"therapeutic_area_only", "broad_candidate"}:
        return "needs_review", "Broad oncology/CPV candidate without direct molecule evidence"
    return "needs_review", "Insufficient evidence for automatic acceptance"


def _pick_canonical_notice(group: pd.DataFrame) -> str:
    """Prefer competition notices with molecule evidence and documents."""
    stage_rank = {
        "competition": 0,
        "prior_information": 10,
        "award": 8,
        "voluntary_ex_ante": 9,
    }
    scored: list[tuple[int, str]] = []
    for _, row in group.iterrows():
        score = stage_rank.get(str(row.get("lifecycleStage", "")), 12)
        if row.get("hasDocumentLinks"):
            score -= 2
        if row.get("hasDirectMoleculeName"):
            score -= 3
        if row.get("hasDirectAtc"):
            score -= 1
        if str(row.get("lifecycleStage", "")) == "competition":
            score -= 10
        scored.append((score, str(row["noticeId"])))
    scored.sort()
    return scored[0][1] if scored else str(group.iloc[0]["noticeId"])


def audit_candidates(candidates_path: Path | None = None) -> AuditResult:
    path = candidates_path or REVIEW_CANDIDATES_CSV
    df = pd.read_csv(path)
    result = AuditResult()

    accepted = df[df["proposedDecision"] == "accepted"].copy()
    result.before_counts = df["proposedDecision"].value_counts().to_dict()

    # Build procedure groups from all candidates for canonical selection
    all_proc_meta: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for _, row in df.iterrows():
        notice_id = str(row["noticeId"])
        target = str(row["targetMolecule"])
        notice = _enrich_from_cached_xml(
            NoticeRecord(
                notice_id=notice_id,
                tender_ref=str(row.get("tenderRef", "") or ""),
                title=str(row.get("title", "") or ""),
                notice_type=str(row.get("noticeType", "") or ""),
            )
        )
        pk = procedure_key(notice.tender_ref, notice.title, notice_id)
        all_proc_meta.setdefault((target, pk), []).append(
            {
                "noticeId": notice_id,
                "lifecycleStage": _lifecycle_stage(str(row.get("noticeType", ""))),
                "hasDocumentLinks": bool(str(row.get("documentUrls", "") or "") not in ("", "nan")),
                "hasDirectMoleculeName": bool(
                    match_evidence(f"{notice.title} {notice.description}", context="notice")
                    and match_evidence(f"{notice.title} {notice.description}", context="notice").molecule_detected
                ),
                "hasDirectAtc": False,
            }
        )

    audited: list[dict[str, Any]] = []
    proc_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for _, row in accepted.iterrows():
        notice_id = str(row["noticeId"])
        target = str(row["targetMolecule"])
        notice = NoticeRecord(
            notice_id=notice_id,
            tender_ref=str(row.get("tenderRef", "") or ""),
            title=str(row.get("title", "") or ""),
            buyer=str(row.get("buyer", "") or ""),
            notice_type=str(row.get("noticeType", "") or ""),
            publication_date=str(row.get("publicationDate", "") or ""),
            source_url=str(row.get("noticeUrl", "") or ""),
        )
        notice = _enrich_from_cached_xml(notice)
        xml_path = CACHE_DIR / "ted_xml" / f"{notice_id}.xml"
        xml_text = xml_path.read_text(encoding="utf-8", errors="ignore") if xml_path.exists() else ""

        notice_text, _ = _notice_text(notice, xml_text)
        match = match_evidence(notice_text, context="notice")
        evidence_level, exact_text, evidence_loc, has_name, has_atc = _classify_evidence(
            target, notice_text, xml_text, match
        )
        doc_urls = str(row.get("documentUrls", "") or "")
        has_docs = bool(doc_urls and doc_urls != "nan")

        pk = procedure_key(notice.tender_ref, notice.title, notice_id)
        audited_decision, audit_reason = _audited_decision(
            evidence_level, "accepted", notice, target
        )

        entry = {
            "targetMolecule": target,
            "queryUsed": row.get("queryUsed", ""),
            "noticeId": notice_id,
            "tenderRef": notice.tender_ref or row.get("tenderRef", ""),
            "title": notice.title or row.get("title", ""),
            "buyer": notice.buyer or row.get("buyer", ""),
            "publicationDate": row.get("publicationDate", ""),
            "lifecycleStage": _lifecycle_stage(str(row.get("noticeType", ""))),
            "noticeUrl": row.get("noticeUrl", ""),
            "documentUrls": doc_urls if doc_urls != "nan" else "",
            "procedureKey": pk,
            "evidenceLevel": evidence_level,
            "exactMatchedText": exact_text,
            "evidenceLocation": evidence_loc,
            "hasDirectMoleculeName": has_name,
            "hasDirectAtc": has_atc,
            "hasDocumentLinks": has_docs,
            "likelyLanguageDuplicate": False,
            "likelyLifecycleRelated": False,
            "canonicalProcedureCandidate": False,
            "requiresDocumentConfirmation": evidence_level
            in {"brand_only", "therapeutic_area_only", "broad_candidate"},
            "originalDecision": "accepted",
            "auditedDecision": audited_decision,
            "auditReason": audit_reason,
            "possibleDuplicateOf": row.get("possibleDuplicateOf", ""),
        }
        audited.append(entry)
        proc_groups.setdefault((target, pk), []).append(entry)
        result.evidence_level_counts[evidence_level] = (
            result.evidence_level_counts.get(evidence_level, 0) + 1
        )

    audit_df = pd.DataFrame(audited)
    canonical_by_proc: dict[tuple[str, str], str] = {}
    for key, meta_rows in all_proc_meta.items():
        canonical_by_proc[key] = _pick_canonical_notice(pd.DataFrame(meta_rows))

    for (target, pk), entries in proc_groups.items():
        canonical_id = canonical_by_proc.get((target, pk), entries[0]["noticeId"])
        for entry in entries:
            entry["canonicalProcedureCandidate"] = entry["noticeId"] == canonical_id
            entry["likelyLifecycleRelated"] = len(entries) > 1
            if len(entries) > 1 and entry["noticeId"] != canonical_id:
                entry["likelyLanguageDuplicate"] = True
                entry["possibleDuplicateOf"] = canonical_id

    for entry in audited:
        if entry["likelyLanguageDuplicate"] and not entry["canonicalProcedureCandidate"]:
            entry["auditedDecision"] = "needs_review"
            entry["auditReason"] = "Non-canonical duplicate within procedure; prefer " + entry["possibleDuplicateOf"]
        elif entry["lifecycleStage"] in {"prior_information", "award", "voluntary_ex_ante"}:
            entry["auditedDecision"] = "needs_review"
            entry["auditReason"] = (
                f"Lifecycle stage {entry['lifecycleStage']} is not the pack-bearing competition notice"
            )
        if entry["likelyLanguageDuplicate"]:
            result.language_duplicate_count += 1
        if entry["likelyLifecycleRelated"]:
            result.lifecycle_related_count += 1
        if entry["originalDecision"] == "accepted" and entry["auditedDecision"] != "accepted":
            result.downgrades.append(entry)

    procedure_rows: list[dict[str, Any]] = []
    for (target, pk), entries in proc_groups.items():
        group = pd.DataFrame(entries)
        stages = sorted(set(group["lifecycleStage"].astype(str)))
        canonical = canonical_by_proc.get((target, pk), _pick_canonical_notice(group))
        award_ids = group[group["lifecycleStage"] == "award"]["noticeId"].tolist()
        best_evidence = group["evidenceLevel"].iloc[0]
        procedure_rows.append(
            {
                "targetMolecule": target,
                "procedureKey": pk,
                "noticeCount": len(group),
                "lifecycleStages": ";".join(stages),
                "noticeIds": ";".join(group["noticeId"].astype(str)),
                "acceptedEvidenceLevel": best_evidence,
                "hasDocumentLinks": bool(group["hasDocumentLinks"].any()),
                "canonicalPackBearingNotice": canonical,
                "awardNoticeId": award_ids[0] if award_ids else "",
                "recommendedFetchPriority": "high" if group["hasDocumentLinks"].any() else "medium",
            }
        )
        result.procedures_by_molecule[target] = result.procedures_by_molecule.get(target, 0) + 1

    result.audited_rows = audited
    result.procedure_rows = procedure_rows

    all_df = df.copy()
    downgraded_to = {}
    for entry in audited:
        downgraded_to[(entry["noticeId"], entry["targetMolecule"])] = entry["auditedDecision"]

    after = dict(result.before_counts)
    for entry in audited:
        if entry["auditedDecision"] != "accepted":
            after["accepted"] = max(0, after.get("accepted", 0) - 1)
            after[entry["auditedDecision"]] = after.get(entry["auditedDecision"], 0) + 1
    result.after_counts = after

    # Re-write audited CSV after downgrade pass
    pd.DataFrame(audited, columns=AUDIT_COLUMNS).to_csv(
        AUDITED_CANDIDATES_CSV, index=False, encoding="utf-8"
    )
    pd.DataFrame(procedure_rows, columns=PROCEDURE_COLUMNS).to_csv(
        PROCEDURE_SUMMARY_CSV, index=False, encoding="utf-8"
    )
    return result
