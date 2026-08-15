from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from norway_tenders.discovery.audit import audit_candidates
from norway_tenders.discovery.axitinib_gap import run_axitinib_gap_search
from norway_tenders.discovery.document_access import classify_urls
from norway_tenders.settings import (
    AXITINIB_GAP_REPORT,
    DOCUMENT_ACCESS_CSV,
    PHASE4B_REPORT,
    PROCEDURE_SUMMARY_CSV,
    REVIEW_CANDIDATES_CSV,
)

logger = logging.getLogger(__name__)


@dataclass
class Phase4BResult:
    axitinib_queries: int = 0
    axitinib_notices: int = 0
    axitinib_families: list[str] = field(default_factory=list)
    axitinib_confirmation: str = ""
    axitinib_errors: list[str] = field(default_factory=list)
    audit: Any = None
    document_access: list[dict[str, Any]] = field(default_factory=list)
    fetch_queue: list[dict[str, Any]] = field(default_factory=list)
    report_path: Path | None = None


def _is_document_url(url: str) -> bool:
    u = url.lower()
    if "mercell.com/m/file/getfile" in u:
        return True
    if "permalink.mercell" in u:
        return True
    if u.endswith(".pdf") or u.endswith(".xlsx") or u.endswith(".xls"):
        return True
    return False


def _collect_urls(candidates_df: pd.DataFrame, gap_df: pd.DataFrame) -> list[str]:
    urls: list[str] = []
    for frame in (candidates_df, gap_df):
        for col in ("documentUrls", "documentUrl"):
            if col not in frame.columns:
                continue
            for val in frame[col].dropna():
                urls.extend(u.strip() for u in str(val).split(";") if u.strip())
    return [u for u in urls if _is_document_url(u)]


def _build_fetch_queue(procedure_summary: pd.DataFrame) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    molecules = ["Axitinib", "Everolimus", "Lenalidomide", "Anagrelide", "Paliperidone"]
    counts = {m: 0 for m in molecules}

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    sorted_procs = procedure_summary.copy()
    sorted_procs["_pr"] = sorted_procs["recommendedFetchPriority"].map(
        lambda x: priority_rank.get(str(x), 3)
    )
    sorted_procs = sorted_procs.sort_values(by=["_pr", "hasDocumentLinks"], ascending=[True, False])

    for _, proc in sorted_procs.iterrows():
        mol = str(proc["targetMolecule"])
        if counts.get(mol, 0) >= 2:
            continue
        queue.append(
            {
                "targetMolecule": mol,
                "procedureKey": proc["procedureKey"],
                "canonicalNoticeId": proc["canonicalPackBearingNotice"],
                "awardNoticeId": proc.get("awardNoticeId", "") or "",
                "priority": proc["recommendedFetchPriority"],
                "reason": "Canonical pack-bearing notice from procedure summary",
            }
        )
        counts[mol] = counts.get(mol, 0) + 1

    for mol in molecules:
        if counts[mol] == 0:
            queue.append(
                {
                    "targetMolecule": mol,
                    "procedureKey": "",
                    "canonicalNoticeId": "",
                    "priority": "high",
                    "reason": "No audited procedure; requires expanded discovery (e.g. Axitinib gap)",
                }
            )
    return queue


def run_phase4b(*, offline: bool = False, refresh: bool = False) -> Phase4BResult:
    result = Phase4BResult()

    gap = run_axitinib_gap_search(offline=offline, refresh=refresh)
    result.axitinib_queries = gap.queries_executed
    result.axitinib_notices = len(gap.notice_ids)
    result.axitinib_families = sorted(gap.tender_families)
    result.axitinib_confirmation = gap.confirmation
    result.axitinib_errors = gap.errors

    audit = audit_candidates()
    result.audit = audit

    candidates_df = pd.read_csv(REVIEW_CANDIDATES_CSV)
    gap_df = pd.read_csv(AXITINIB_GAP_REPORT)
    urls = _collect_urls(candidates_df, gap_df)
    access_results = classify_urls(urls, use_cache=True)
    access_rows = [r.to_dict() for r in access_results]
    result.document_access = access_rows
    DOCUMENT_ACCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(access_rows).to_csv(DOCUMENT_ACCESS_CSV, index=False, encoding="utf-8")

    procedure_df = pd.read_csv(PROCEDURE_SUMMARY_CSV)
    result.fetch_queue = _build_fetch_queue(procedure_df)

    # Enrich Axitinib queue from gap report oncology families
    gap_df = pd.read_csv(AXITINIB_GAP_REPORT)
    ax_proc = gap_df[
        gap_df["title"].astype(str).str.contains("LIS 2207|LIS 2107", case=False, na=False)
    ]
    if len(ax_proc):
        row = ax_proc.iloc[0]
        result.fetch_queue = [
            q for q in result.fetch_queue if q["targetMolecule"] != "Axitinib"
        ] + [
            {
                "targetMolecule": "Axitinib",
                "procedureKey": str(row.get("tenderRef", "LIS2207")),
                "canonicalNoticeId": str(row.get("noticeId", "")),
                "priority": "high",
                "reason": "Oncology umbrella tender; Axitinib confirmation requires Virkestoffliste/Prisskjema",
            }
        ]

    report = {
        "axitinib_queries_executed": result.axitinib_queries,
        "axitinib_candidate_notices": result.axitinib_notices,
        "axitinib_tender_families": result.axitinib_families,
        "axitinib_confirmation": result.axitinib_confirmation,
        "axitinib_errors": result.axitinib_errors,
        "accepted_evidence_level_counts": audit.evidence_level_counts,
        "procedures_by_molecule": audit.procedures_by_molecule,
        "language_duplicate_count": audit.language_duplicate_count,
        "lifecycle_related_count": audit.lifecycle_related_count,
        "candidate_counts_before": audit.before_counts,
        "candidate_counts_after_audit": audit.after_counts,
        "document_access_counts": (
            pd.DataFrame(access_rows)["accessClass"].value_counts().to_dict() if access_rows else {}
        ),
        "downgrade_examples": [
            {
                "noticeId": d["noticeId"],
                "targetMolecule": d["targetMolecule"],
                "title": d["title"],
                "evidenceLevel": d["evidenceLevel"],
                "originalDecision": d["originalDecision"],
                "auditedDecision": d["auditedDecision"],
                "auditReason": d["auditReason"],
            }
            for d in audit.downgrades[:5]
        ],
        "recommended_fetch_queue": result.fetch_queue,
    }
    PHASE4B_REPORT.parent.mkdir(parents=True, exist_ok=True)
    PHASE4B_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    result.report_path = PHASE4B_REPORT
    return result
