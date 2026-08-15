from __future__ import annotations

import csv
import logging
from pathlib import Path

from norway_tenders.models import DocumentRecord, NoticeRecord
from norway_tenders.settings import SEEDS_DIR, SOURCES_SEED

logger = logging.getLogger(__name__)


def resolve_seed_path(filename: str) -> Path | None:
    """Return local path for a seed document if present under data/seeds/."""
    if not filename:
        return None
    candidate = SEEDS_DIR / filename
    if candidate.exists() and candidate.read_bytes()[:2] == b"PK":
        return candidate
    if candidate.exists() and candidate.suffix.lower() == ".pdf":
        return candidate
    return None


def _notice_matches_seed(notice: NoticeRecord, row: dict[str, str]) -> bool:
    tender_ref = (row.get("tenderRef") or row.get("tender_ref") or "").upper()
    row_notice = row.get("noticeId") or row.get("notice_id") or ""
    title_norm = notice.title.upper().replace(" ", "")

    if row_notice and row_notice == notice.notice_id:
        return True

    if tender_ref == "LIS2234B":
        return "LIS2234B" in title_norm
    if tender_ref == "LIS2234":
        return "LIS2234" in title_norm and "LIS2234B" not in title_norm

    if tender_ref and tender_ref in title_norm:
        return True
    notice_ref = (notice.tender_ref or "").upper().replace(" ", "")
    if tender_ref and tender_ref in notice_ref:
        return True
    return False


def load_seed_sources(path: Path | None = None) -> list[dict[str, str]]:
    seed_path = path or SOURCES_SEED
    if not seed_path.exists():
        logger.warning("Seed sources file not found: %s", seed_path)
        return []
    with seed_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def seed_notice_crosswalk(seeds: list[dict[str, str]]) -> dict[str, str]:
    """Map TED notice IDs to tender references from sources.csv."""
    crosswalk: dict[str, str] = {}
    for row in seeds:
        notice_id = (row.get("noticeId") or row.get("notice_id") or "").strip()
        tender_ref = (row.get("tenderRef") or row.get("tender_ref") or "").strip()
        if notice_id and tender_ref:
            crosswalk[notice_id] = tender_ref
    return crosswalk


def seeds_for_notice(notice: NoticeRecord, seeds: list[dict[str, str]]) -> list[DocumentRecord]:
    docs: list[DocumentRecord] = []
    for row in seeds:
        if not _notice_matches_seed(notice, row):
            continue
        filename = row.get("filename", "")
        local = resolve_seed_path(filename)
        docs.append(
            DocumentRecord(
                notice_id=notice.notice_id,
                url=row.get("url", ""),
                filename=filename,
                local_path=str(local) if local else "",
            )
        )
    return docs
