from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from norway_tenders.settings import (
    REQUEST_DELAY_SECONDS,
    TED_SEARCH_CACHE_DIR,
    TED_SEARCH_URL,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


def cache_path(query: str) -> Path:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return TED_SEARCH_CACHE_DIR / f"{digest}.json"


def load_cached_search(full_query: str) -> list[dict[str, Any]] | None:
    path = cache_path(full_query)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("notices", [])
    return None


def save_cached_search(full_query: str, notices: list[dict[str, Any]], *, error: str = "") -> None:
    TED_SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"query": full_query, "notices": notices, "error": error}
    cache_path(full_query).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def ted_search(client: httpx.Client, query_fragment: str, limit: int = 100) -> list[dict[str, Any]]:
    full_query = f"buyer-country=NOR AND ({query_fragment})"
    payload = {
        "query": f"{full_query} SORT BY publication-date DESC",
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
    return response.json().get("notices", [])


def execute_queries(
    queries: list[tuple[str, str]],
    *,
    offline: bool = False,
    refresh: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Run TED queries; return {query_label: notices} and errors."""
    results: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=60.0,
    ) as client:
        for label, fragment in queries:
            full_query = f"buyer-country=NOR AND ({fragment})"
            try:
                if offline and not refresh:
                    cached = load_cached_search(full_query)
                    if cached is None:
                        errors.append(f"Cache miss offline: {label}")
                        continue
                    results[label] = cached
                    continue

                cached = load_cached_search(full_query) if not refresh else None
                if cached is not None:
                    results[label] = cached
                else:
                    notices = ted_search(client, fragment)
                    save_cached_search(full_query, notices)
                    results[label] = notices
                    time.sleep(REQUEST_DELAY_SECONDS)
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                save_cached_search(full_query, [], error=str(exc))
                logger.error("TED search failed for %s: %s", label, exc)

    return results, errors
