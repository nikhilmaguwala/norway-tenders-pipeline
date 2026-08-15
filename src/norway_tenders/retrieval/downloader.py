from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from norway_tenders.models import DocumentRecord
from norway_tenders.settings import (
    CACHE_DIR,
    MANIFEST_CACHE,
    RAW_DIR,
    REQUEST_DELAY_SECONDS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

REQUIRED_OFFLINE_TED_XML_NOTICE_IDS: tuple[str, ...] = (
    "196990-2022",
    "300984-2021",
    "404973-2025",
    "244859-2024",
    "682047-2022",
    "434619-2026",
    "335380-2021",
    "48506-2021",
    "147880-2021",
)


class TedXmlCacheMissError(FileNotFoundError):
    """Raised when offline mode requires a cached TED notice XML that is not present."""

    def __init__(self, notice_id: str, cache_path: Path) -> None:
        self.notice_id = notice_id
        self.cache_path = cache_path
        super().__init__(
            f"Offline TED XML cache miss for notice {notice_id}. "
            f"Expected cached file at {cache_path}. "
            "Supply the official TED notice XML at this path before running build --offline."
        )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _download_url(client: httpx.Client, url: str, dest: Path) -> tuple[bool, str]:
    response = client.get(url, follow_redirects=True)
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"
    content = response.content
    if content[:15].startswith(b"<!DOCTYPE") or content[:5].startswith(b"<html"):
        return False, "HTML response (likely blocked or login-gated)"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return True, ""


def fetch_documents(
    documents: list[DocumentRecord],
    *,
    offline: bool = False,
    refresh: bool = False,
) -> list[DocumentRecord]:
    manifest = _load_manifest() if offline and not refresh else {}
    results: list[DocumentRecord] = []

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=120.0,
    ) as client:
        for doc in documents:
            key = f"{doc.notice_id}|{doc.filename or doc.url}"
            if offline and key in manifest and not refresh:
                cached = manifest[key]
                doc.local_path = cached.get("local_path", "")
                doc.sha256 = cached.get("sha256", "")
                doc.download_error = cached.get("download_error", "")
                results.append(doc)
                continue

            if not doc.url:
                doc.download_error = "Missing URL"
                results.append(doc)
                continue

            safe_name = doc.filename or doc.url.split("/")[-1] or "document"
            dest = RAW_DIR / doc.notice_id / safe_name
            if dest.exists() and not refresh and (
                dest.read_bytes()[:2] == b"PK" or dest.suffix.lower() == ".pdf"
            ):
                doc.local_path = str(dest)
                doc.sha256 = sha256_file(dest)
                doc.retrieved_at = datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc)
            else:
                try:
                    ok, err = _download_url(client, doc.url, dest)
                    if ok:
                        doc.local_path = str(dest)
                        doc.sha256 = sha256_file(dest)
                        doc.retrieved_at = datetime.now(timezone.utc)
                    else:
                        doc.download_error = err
                        logger.warning("Download failed %s: %s", doc.url, err)
                except Exception as exc:
                    doc.download_error = str(exc)
                    logger.warning("Download error %s: %s", doc.url, exc)
                time.sleep(REQUEST_DELAY_SECONDS)

            manifest[key] = {
                "local_path": doc.local_path,
                "sha256": doc.sha256,
                "download_error": doc.download_error,
                "url": doc.url,
            }
            results.append(doc)

    _save_manifest(manifest)
    return results


def fetch_ted_xml(notice_id: str, *, offline: bool = False) -> str:
    dest = CACHE_DIR / "ted_xml" / f"{notice_id}.xml"
    if dest.exists():
        return dest.read_text(encoding="utf-8")
    if offline:
        raise TedXmlCacheMissError(notice_id, dest)

    url = f"https://ted.europa.eu/en/notice/{notice_id}/xml"
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60.0) as client:
        time.sleep(REQUEST_DELAY_SECONDS)
        response = client.get(url)
        response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return response.text


def _load_manifest() -> dict:
    if MANIFEST_CACHE.exists():
        return json.loads(MANIFEST_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_CACHE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_CACHE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
