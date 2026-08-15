from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from norway_tenders.matching.matcher import load_molecule_config
from norway_tenders.models import NoticeRecord, Provenance
from norway_tenders.settings import (
    DISCOVERY_CACHE,
    REQUEST_DELAY_SECONDS,
    TED_SEARCH_URL,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

NS = {"ted": "http://publications.europa.eu/resource/schema/ted/R2.0.9/publication"}


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


def _iso_date(value: str | None) -> str:
    if not value:
        return ""
    return value[:10]


def _build_molecule_queries() -> list[str]:
    config = load_molecule_config()
    queries: list[str] = []
    for spec in config.values():
        for name in spec.names:
            queries.append(f'FT~"{name}"')
        for atc in spec.atc_codes:
            queries.append(f'FT~"{atc}"')
    queries.append('FT~"Sykehusinnkjøp" AND FT~"LIS"')
    return queries


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
        ],
        "limit": limit,
        "scope": "ALL",
        "paginationMode": "ITERATION",
    }
    response = client.post(TED_SEARCH_URL, json=payload)
    response.raise_for_status()
    data = response.json()
    return data.get("notices", [])


def discover_ted_notices(*, offline: bool = False, refresh: bool = False) -> list[NoticeRecord]:
    if offline and DISCOVERY_CACHE.exists() and not refresh:
        return _load_cache(DISCOVERY_CACHE)

    notices: dict[str, NoticeRecord] = {}
    queries = _build_molecule_queries()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=60.0,
    ) as client:
        for query in queries:
            try:
                raw_notices = _ted_search(client, query)
                for raw in raw_notices:
                    notice = _parse_ted_notice(raw)
                    if notice:
                        notices[notice.notice_id] = notice
                time.sleep(REQUEST_DELAY_SECONDS)
            except Exception as exc:
                logger.error("TED search failed for query %s: %s", query, exc)

    result = list(notices.values())
    DISCOVERY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    DISCOVERY_CACHE.write_text(
        json.dumps([_notice_to_dict(n) for n in result], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Discovered %d unique TED notices", len(result))
    return result


def _parse_ted_notice(raw: dict[str, Any]) -> NoticeRecord | None:
    notice_id = raw.get("publication-number", "")
    if not notice_id:
        return None

    title = _first_lang(raw.get("notice-title", ""))
    buyer = _first_lang(raw.get("buyer-name", ""))
    description = _first_lang(raw.get("description-proc", ""))
    links = raw.get("links", {})
    source_url = ""
    if isinstance(links, dict):
        html = links.get("html", {})
        if isinstance(html, dict):
            source_url = html.get("ENG", "") or next(iter(html.values()), "")

    tender_ref = ""
    for field in ("internal-identifier-proc", "identifier-lot"):
        val = raw.get(field)
        if val:
            tender_ref = _first_lang(val) if isinstance(val, dict) else str(val)
            break
    if not tender_ref:
        match = re.search(r"LIS\s*\d+[a-z]?", title, re.IGNORECASE)
        if match:
            tender_ref = match.group(0).upper().replace(" ", "")

    notice = NoticeRecord(
        notice_id=notice_id,
        tender_ref=tender_ref,
        title=title,
        buyer=buyer,
        notice_type=raw.get("notice-type", ""),
        publication_date=_iso_date(raw.get("publication-date")),
        procedure_type=raw.get("procedure-type", "") or "",
        source_url=source_url,
        description=description,
        provenance=Provenance(source_url=source_url, filename=f"{notice_id}.json"),
    )
    return notice


def _notice_to_dict(notice: NoticeRecord) -> dict[str, Any]:
    return {
        "notice_id": notice.notice_id,
        "tender_ref": notice.tender_ref,
        "title": notice.title,
        "buyer": notice.buyer,
        "notice_type": notice.notice_type,
        "publication_date": notice.publication_date,
        "procedure_type": notice.procedure_type,
        "source_url": notice.source_url,
        "description": notice.description,
    }


def _load_cache(path: Path) -> list[NoticeRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        NoticeRecord(
            notice_id=d["notice_id"],
            tender_ref=d.get("tender_ref", ""),
            title=d.get("title", ""),
            buyer=d.get("buyer", ""),
            notice_type=d.get("notice_type", ""),
            publication_date=d.get("publication_date", ""),
            procedure_type=d.get("procedure_type", ""),
            source_url=d.get("source_url", ""),
            description=d.get("description", ""),
        )
        for d in data
    ]
