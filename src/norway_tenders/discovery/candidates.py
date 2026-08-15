from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from norway_tenders.discovery.seeds import load_seed_sources, seed_notice_crosswalk
from norway_tenders.matching.evidence import EXAMPLE_CONTEXT, PHARMA_BUYERS, is_pharmaceutical_evidence
from norway_tenders.matching.matcher import load_molecule_config, match_evidence
from norway_tenders.models import MoleculeMatch, NoticeRecord
from norway_tenders.parsers.ted_xml import enrich_notice_from_xml
from norway_tenders.settings import (
    CACHE_DIR,
    DISCOVERY_LOG,
    DISCOVERY_SUMMARY,
    LIS_BUYER_NAMES,
    REQUEST_DELAY_SECONDS,
    REVIEW_CANDIDATE_COLUMNS,
    REVIEW_CANDIDATES_CSV,
    TED_SEARCH_CACHE_DIR,
    TED_SEARCH_URL,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

BRAND_ONLY_MARKERS = re.compile(
    r"\b(revlimid|inlyta|afinitor|certican|zortress|agrylin|xeplion|invega|trevicta)\b",
    re.I,
)

LIFECYCLE_STAGE_MAP = {
    "cn-standard": "competition",
    "cn-desg": "competition",
    "3": "competition",
    "can-standard": "award",
    "can-desg": "award",
    "7": "award",
    "pin-buyer": "prior_information",
    "pin-rtl": "prior_information",
    "pin-cfc-standard": "prior_information",
    "0": "prior_information",
    "veat": "voluntary_ex_ante",
    "corr": "corrigendum",
}


@dataclass
class DiscoveryQuery:
    target_molecule: str
    query: str
    query_label: str


@dataclass
class ReviewCandidate:
    target_molecule: str
    query_used: str
    matched_term: str
    detection_method: str
    notice_id: str
    tender_ref: str
    title: str
    buyer: str
    publication_date: str
    notice_type: str
    status: str
    lifecycle_stage: str
    estimated_value: float | None
    currency: str
    notice_url: str
    document_urls: str
    proposed_decision: str
    decision_reason: str
    language: str
    possible_duplicate_of: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "targetMolecule": self.target_molecule,
            "queryUsed": self.query_used,
            "matchedTerm": self.matched_term,
            "detectionMethod": self.detection_method,
            "noticeId": self.notice_id,
            "tenderRef": self.tender_ref,
            "title": self.title,
            "buyer": self.buyer,
            "publicationDate": self.publication_date,
            "noticeType": self.notice_type,
            "status": self.status,
            "lifecycleStage": self.lifecycle_stage,
            "estimatedValue": self.estimated_value,
            "currency": self.currency,
            "noticeUrl": self.notice_url,
            "documentUrls": self.document_urls,
            "proposedDecision": self.proposed_decision,
            "decisionReason": self.decision_reason,
            "language": self.language,
            "possibleDuplicateOf": self.possible_duplicate_of,
        }


@dataclass
class DiscoveryRunResult:
    candidates: list[ReviewCandidate] = field(default_factory=list)
    queries_by_molecule: dict[str, int] = field(default_factory=dict)
    candidates_by_molecule: dict[str, int] = field(default_factory=dict)
    decision_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    lis2234_matches: list[ReviewCandidate] = field(default_factory=list)


def _first_lang(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("eng", "en", "nor", "nob", "nno"):
            if key in value and value[key]:
                v = value[key]
                return v[0] if isinstance(v, list) else str(v)
        for v in value.values():
            if v:
                return v[0] if isinstance(v, list) else str(v)
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value) if value else ""


def _detect_language(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("eng", "en"):
            if key in value and value[key]:
                return "en"
        for key in ("nor", "nob", "nno"):
            if key in value and value[key]:
                return "no"
    return ""


def _iso_date(value: str | None) -> str:
    if not value:
        return ""
    return value[:10]


def _cache_path(query: str) -> Path:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return TED_SEARCH_CACHE_DIR / f"{digest}.json"


def _load_cached_search(query: str) -> list[dict[str, Any]] | None:
    path = _cache_path(query)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("notices", [])
    return None


def _save_cached_search(query: str, notices: list[dict[str, Any]], *, error: str = "") -> None:
    TED_SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"query": query, "notices": notices, "error": error}
    _cache_path(query).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _ted_search(client: httpx.Client, query: str, limit: int = 100) -> list[dict[str, Any]]:
    payload = {
        "query": f"buyer-country=NOR AND ({query}) SORT BY publication-date DESC",
        "fields": [
            "publication-number",
            "notice-title",
            "buyer-name",
            "publication-date",
            "notice-type",
            "procedure-type",
            "links",
            "description-proc",
            "identifier-lot",
            "internal-identifier-proc",
            "estimated-value-proc",
            "classification-cpv",
        ],
        "limit": limit,
        "scope": "ALL",
        "paginationMode": "ITERATION",
    }
    response = client.post(TED_SEARCH_URL, json=payload)
    response.raise_for_status()
    data = response.json()
    return data.get("notices", [])


def build_discovery_queries(seeds: list[dict[str, str]] | None = None) -> list[DiscoveryQuery]:
    config = load_molecule_config()
    queries: list[DiscoveryQuery] = []
    seeds = seeds or load_seed_sources()

    for molecule, spec in config.items():
        for name in spec.names:
            queries.append(
                DiscoveryQuery(molecule, f'FT~"{name}"', f"name:{name}")
            )
        for atc in spec.atc_codes:
            queries.append(
                DiscoveryQuery(molecule, f'FT~"{atc}"', f"atc:{atc}")
            )
        for buyer in LIS_BUYER_NAMES[:2]:
            queries.append(
                DiscoveryQuery(
                    molecule,
                    f'FT~"{buyer}" AND FT~"{spec.names[0]}"',
                    f"buyer+name:{buyer}",
                )
            )

    queries.append(
        DiscoveryQuery("Lenalidomide", 'FT~"LIS 2234"', "tender_ref:LIS 2234")
    )
    for buyer in LIS_BUYER_NAMES:
        queries.append(
            DiscoveryQuery(
                "Lenalidomide",
                f'FT~"{buyer}" AND FT~"LIS 2234"',
                f"buyer+tender:{buyer}",
            )
        )

    for notice_id, tender_ref in seed_notice_crosswalk(seeds).items():
        molecule = "Lenalidomide" if "2234" in tender_ref.upper() else "Unknown"
        if tender_ref.upper().startswith("LIS"):
            for mol in config:
                if mol in tender_ref or mol == "Lenalidomide":
                    molecule = mol
                    break
        queries.append(
            DiscoveryQuery(
                molecule if molecule != "Unknown" else "Lenalidomide",
                f'FT~"{notice_id}"',
                f"seed_notice:{notice_id}->{tender_ref}",
            )
        )

    return queries


def _enrich_from_cached_xml(notice: NoticeRecord) -> NoticeRecord:
    xml_path = CACHE_DIR / "ted_xml" / f"{notice.notice_id}.xml"
    if xml_path.exists():
        try:
            return enrich_notice_from_xml(notice, xml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Cached XML enrich failed for %s: %s", notice.notice_id, exc)
    return notice


def _apply_seed_crosswalk(
    notice: NoticeRecord,
    crosswalk: dict[str, str],
) -> NoticeRecord:
    if notice.notice_id in crosswalk:
        seed_ref = crosswalk[notice.notice_id]
        if seed_ref.upper().startswith("LIS"):
            notice.tender_ref = seed_ref
    return notice


def _is_lis2234_notice(
    notice: NoticeRecord,
    crosswalk: dict[str, str],
) -> bool:
    ref = crosswalk.get(notice.notice_id, "")
    if ref.upper().replace(" ", "") in {"LIS2234", "LIS2234B"}:
        return True
    blob = f"{notice.tender_ref} {notice.title} {notice.description}"
    return bool(re.search(r"\blis\s*2234\b", blob, re.I))


def _parse_notice(raw: dict[str, Any]) -> NoticeRecord:
    notice_id = raw.get("publication-number", "")
    title = _first_lang(raw.get("notice-title", ""))
    buyer = _first_lang(raw.get("buyer-name", ""))
    description = _first_lang(raw.get("description-proc", ""))
    links = raw.get("links", {})
    source_url = ""
    if isinstance(links, dict):
        html = links.get("html", {})
        if isinstance(html, dict):
            source_url = html.get("ENG", "") or html.get("NOR", "") or next(iter(html.values()), "")

    tender_ref = ""
    for field_name in ("internal-identifier-proc", "identifier-lot"):
        val = raw.get(field_name)
        if val:
            tender_ref = _first_lang(val) if isinstance(val, dict) else str(val)
            break
    if not tender_ref:
        match = re.search(r"LIS\s*\d+[a-z]?", title, re.IGNORECASE)
        if match:
            tender_ref = match.group(0).upper().replace(" ", "")

    estimated_value = None
    currency = "NOK"
    est = raw.get("estimated-value-proc")
    if isinstance(est, dict):
        for val in est.values():
            if isinstance(val, list) and val:
                try:
                    estimated_value = float(str(val[0]).replace(",", "."))
                except ValueError:
                    pass
            break
    elif est is not None:
        try:
            estimated_value = float(str(est).replace(",", "."))
        except ValueError:
            pass

    return NoticeRecord(
        notice_id=notice_id,
        tender_ref=tender_ref,
        title=title,
        buyer=buyer,
        notice_type=raw.get("notice-type", "") or "",
        publication_date=_iso_date(raw.get("publication-date")),
        procedure_type=raw.get("procedure-type", "") or "",
        source_url=source_url,
        description=description,
        estimated_value=estimated_value,
        currency=currency,
    )


def _lifecycle_stage(notice_type: str) -> str:
    nt = (notice_type or "").casefold()
    for key, stage in LIFECYCLE_STAGE_MAP.items():
        if key in nt:
            return stage
    return notice_type or "unknown"


def _notice_status_from_type(notice_type: str) -> str:
    nt = (notice_type or "").casefold()
    if "can" in nt or "award" in nt:
        return "awarded"
    if "cn" in nt or "competition" in nt:
        return "open"
    if "pin" in nt:
        return "planned"
    if "corr" in nt:
        return "corrected"
    return ""


def _document_urls_for_notice(notice_id: str, seeds: list[dict[str, str]]) -> str:
    urls = [
        row.get("url", "")
        for row in seeds
        if (row.get("noticeId") or row.get("notice_id")) == notice_id and row.get("url")
    ]
    return ";".join(urls)


def _classify_candidate(
    notice: NoticeRecord,
    match: MoleculeMatch | None,
    *,
    target_molecule: str,
) -> tuple[str, str]:
    text = f"{notice.title} {notice.description}"
    buyer = notice.buyer.casefold()

    if not match:
        if BRAND_ONLY_MARKERS.search(text) and not re.search(
            r"\b(lenalidomid|lenalidomide|everolimus|anagrelid|anagrelide|paliperidon|paliperidone|axitinib)\b",
            text,
            re.I,
        ):
            return "rejected", "Brand-only mention without configured molecule name"
        return "rejected", "No molecule name or ATC evidence in notice text"

    if EXAMPLE_CONTEXT.search(text) and (match.molecule_variant or "").casefold() not in notice.title.casefold():
        if buyer not in PHARMA_BUYERS:
            return "rejected", "Molecule appears only as illustrative example in non-pharma context"

    if match.product_molecule != target_molecule and target_molecule != "LIS 2234":
        return "rejected", f"Matched {match.product_molecule}, not target {target_molecule}"

    if is_pharmaceutical_evidence(notice, match):
        if buyer in PHARMA_BUYERS or re.search(r"\blis\s*\d+", notice.title, re.I):
            return "accepted", "Pharmaceutical evidence with Sykehusinnkjøp or LIS tender reference"
        if match.molecule_detected:
            return "needs_review", "Molecule name present but buyer is not Sykehusinnkjøp"
        return "needs_review", "ATC-only match; requires manual confirmation"

    if match.detection_method.startswith("atc"):
        return "needs_review", "ATC present but weak pharmaceutical context"

    return "rejected", "Insufficient pharmaceutical evidence; possible broad CPV hit"


def _candidate_from_notice(
    notice: NoticeRecord,
    raw: dict[str, Any],
    query: DiscoveryQuery,
    seeds: list[dict[str, str]],
    *,
    duplicate_index: dict[str, str],
) -> ReviewCandidate | None:
    text = f"{notice.title} {notice.description} {notice.tender_ref}"
    match = match_evidence(text, context="notice")
    if not match and query.query_label.startswith("tender_ref"):
        if re.search(r"lis\s*2234", text, re.I):
            match = MoleculeMatch(
                product_molecule="Lenalidomide",
                molecule_detected=False,
                detection_method="atc_in_notice",
                atc_code="",
                matched_text="LIS 2234",
            )

    decision, reason = _classify_candidate(notice, match, target_molecule=query.target_molecule)

    matched_term = ""
    detection_method = ""
    if match:
        matched_term = match.molecule_variant or match.atc_code or match.matched_text
        detection_method = match.detection_method
    elif query.query_label.startswith("tender_ref"):
        matched_term = "LIS 2234"

    doc_urls = _document_urls_for_notice(notice.notice_id, seeds)
    if doc_urls and "mercell.com" in doc_urls:
        decision = "login_wall" if decision == "accepted" else decision
        reason = f"{reason}; Mercell document URLs require login"

    language = _detect_language(raw.get("notice-title", "")) or _detect_language(
        raw.get("description-proc", "")
    )

    tender_key = (notice.tender_ref or "").upper().replace(" ", "")
    possible_dup = ""
    if tender_key and tender_key in duplicate_index and duplicate_index[tender_key] != notice.notice_id:
        possible_dup = duplicate_index[tender_key]

    candidate = ReviewCandidate(
        target_molecule=query.target_molecule,
        query_used=query.query_label,
        matched_term=matched_term,
        detection_method=detection_method,
        notice_id=notice.notice_id,
        tender_ref=notice.tender_ref,
        title=notice.title,
        buyer=notice.buyer,
        publication_date=notice.publication_date,
        notice_type=notice.notice_type,
        status=_notice_status_from_type(notice.notice_type),
        lifecycle_stage=_lifecycle_stage(notice.notice_type),
        estimated_value=notice.estimated_value,
        currency=notice.currency,
        notice_url=notice.source_url,
        document_urls=doc_urls,
        proposed_decision=decision,
        decision_reason=reason,
        language=language,
        possible_duplicate_of=possible_dup,
    )
    return candidate


def run_discovery(*, offline: bool = False, refresh: bool = False) -> DiscoveryRunResult:
    """Execute TED discovery and write review_candidates.csv."""
    DISCOVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(DISCOVERY_LOG, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    result = DiscoveryRunResult()
    queries = build_discovery_queries()
    seeds = load_seed_sources()
    crosswalk = seed_notice_crosswalk(seeds)

    for q in queries:
        result.queries_by_molecule[q.target_molecule] = (
            result.queries_by_molecule.get(q.target_molecule, 0) + 1
        )

    seen: set[tuple[str, str, str]] = set()
    duplicate_index: dict[str, str] = {}

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=60.0,
    ) as client:
        for query in queries:
            full_query = f"buyer-country=NOR AND ({query.query})"
            raw_notices: list[dict[str, Any]] = []
            try:
                if offline and not refresh:
                    cached = _load_cached_search(full_query)
                    if cached is not None:
                        raw_notices = cached
                    else:
                        result.errors.append(f"Cache miss offline: {query.query_label}")
                        continue
                else:
                    cached = _load_cached_search(full_query) if not refresh else None
                    if cached is not None:
                        raw_notices = cached
                    else:
                        raw_notices = _ted_search(client, query.query)
                        _save_cached_search(full_query, raw_notices)
                        time.sleep(REQUEST_DELAY_SECONDS)
            except Exception as exc:
                result.errors.append(f"{query.query_label}: {exc}")
                _save_cached_search(full_query, [], error=str(exc))
                logger.error("TED search failed for %s: %s", query.query_label, exc)
                continue

            for raw in raw_notices:
                notice = _parse_notice(raw)
                if not notice.notice_id:
                    continue
                notice = _enrich_from_cached_xml(notice)
                notice = _apply_seed_crosswalk(notice, crosswalk)

                key = (query.target_molecule, notice.notice_id, query.query_label)
                if key in seen:
                    continue
                seen.add(key)

                tender_key = (notice.tender_ref or "").upper().replace(" ", "")
                if tender_key and tender_key not in duplicate_index:
                    duplicate_index[tender_key] = notice.notice_id

                candidate = _candidate_from_notice(
                    notice,
                    raw,
                    query,
                    seeds,
                    duplicate_index=duplicate_index,
                )
                if candidate is None:
                    continue

                result.candidates.append(candidate)
                result.candidates_by_molecule[query.target_molecule] = (
                    result.candidates_by_molecule.get(query.target_molecule, 0) + 1
                )
                result.decision_counts[candidate.proposed_decision] = (
                    result.decision_counts.get(candidate.proposed_decision, 0) + 1
                )

                if _is_lis2234_notice(notice, crosswalk):
                    result.lis2234_matches.append(candidate)

    write_review_candidates(result.candidates)
    _write_summary(result)
    logger.removeHandler(file_handler)
    file_handler.close()
    return result


def write_review_candidates(candidates: list[ReviewCandidate], path: Path | None = None) -> Path:
    out = path or REVIEW_CANDIDATES_CSV
    out.parent.mkdir(parents=True, exist_ok=True)
    records = [c.to_dict() for c in candidates]
    df = pd.DataFrame(records, columns=REVIEW_CANDIDATE_COLUMNS)
    df.to_csv(out, index=False, encoding="utf-8")
    return out


def _write_summary(result: DiscoveryRunResult) -> None:
    summary = {
        "queries_by_molecule": result.queries_by_molecule,
        "candidates_by_molecule": result.candidates_by_molecule,
        "decision_counts": result.decision_counts,
        "errors": result.errors,
        "lis2234_notice_ids": sorted({c.notice_id for c in result.lis2234_matches}),
        "lis2234_identification": (
            "TED notice 300984-2021 identified via seed crosswalk (sources.csv) and "
            "cached TED XML title 'LIS 2234 Lenalidomide'; competition notice cn-standard "
            "published 2021-06-15 by Sykehusinnkjøp HF"
            if any(c.notice_id == "300984-2021" for c in result.lis2234_matches)
            else "Matched by tender reference text or seed crosswalk"
        ),
        "lis2234_urls": [
            {
                "noticeId": c.notice_id,
                "noticeUrl": c.notice_url,
                "documentUrls": c.document_urls,
                "proposedDecision": c.proposed_decision,
            }
            for c in result.lis2234_matches
        ],
        "total_candidates": len(result.candidates),
    }
    DISCOVERY_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    DISCOVERY_SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
