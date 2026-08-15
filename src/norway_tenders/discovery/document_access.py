from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from norway_tenders.settings import (
    DOCUMENT_ACCESS_CACHE_DIR,
    REQUEST_DELAY_SECONDS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

ACCESS_CLASSES = (
    "public_file",
    "public_landing_page",
    "login_wall",
    "missing",
    "invalid_content",
)

FILE_SIGNATURES = {
    "xlsx": b"PK",
    "pdf": b"%PDF",
    "zip": b"PK",
}


@dataclass
class DocumentAccessResult:
    url: str
    access_class: str
    http_status: int | None
    content_type: str
    content_length: int | None
    file_signature: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "accessClass": self.access_class,
            "httpStatus": self.http_status,
            "contentType": self.content_type,
            "contentLength": self.content_length,
            "fileSignature": self.file_signature,
            "reason": self.reason,
        }


def _cache_file(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return DOCUMENT_ACCESS_CACHE_DIR / f"{digest}.json"


def _load_cache(url: str) -> DocumentAccessResult | None:
    path = _cache_file(url)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return DocumentAccessResult(
            url=data.get("url", url),
            access_class=data.get("accessClass", data.get("access_class", "")),
            http_status=data.get("httpStatus", data.get("http_status")),
            content_type=data.get("contentType", data.get("content_type", "")),
            content_length=data.get("contentLength", data.get("content_length")),
            file_signature=data.get("fileSignature", data.get("file_signature", "")),
            reason=data.get("reason", ""),
        )
    return None


def _save_cache(result: DocumentAccessResult) -> None:
    DOCUMENT_ACCESS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_file(result.url).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _detect_signature(body: bytes) -> str:
    if body.startswith(b"%PDF"):
        return "pdf"
    if body.startswith(b"PK"):
        return "xlsx/zip"
    if body[:15].lower().startswith(b"<!doctype") or body[:5].lower().startswith(b"<html"):
        return "html"
    return body[:8].hex() if body else ""


def _classify_body(
    url: str,
    status: int | None,
    content_type: str,
    body: bytes,
) -> DocumentAccessResult:
    sig = _detect_signature(body)
    ct = (content_type or "").lower()

    if status is None or status == 0:
        return DocumentAccessResult(url, "missing", status, content_type, None, sig, "No response")

    if status >= 400:
        if status == 403 and "mercell" in url.lower():
            return DocumentAccessResult(
                url, "login_wall", status, content_type, len(body), sig, "HTTP 403 on Mercell file URL"
            )
        return DocumentAccessResult(
            url, "missing", status, content_type, len(body), sig, f"HTTP {status}"
        )

    if sig in {"pdf", "xlsx/zip"}:
        return DocumentAccessResult(
            url,
            "public_file",
            status,
            content_type,
            len(body),
            sig,
            f"Binary file signature {sig}",
        )

    if "application/vnd.openxmlformats" in ct or "application/pdf" in ct:
        return DocumentAccessResult(
            url, "public_file", status, content_type, len(body), sig, "MIME indicates file"
        )

    if sig == "html" or "text/html" in ct:
        text = body.decode("utf-8", errors="ignore").lower()
        if any(
            token in text
            for token in ("login", "logg inn", "sign in", "captcha", "cloudflare", "access denied")
        ):
            return DocumentAccessResult(
                url,
                "login_wall",
                status,
                content_type,
                len(body),
                sig,
                "HTML login or access gate",
            )
        if "mercell" in url.lower() or "doffin" in url.lower() or "permalink" in url.lower():
            return DocumentAccessResult(
                url,
                "public_landing_page",
                status,
                content_type,
                len(body),
                sig,
                "Procurement portal landing page",
            )
        return DocumentAccessResult(
            url,
            "public_landing_page",
            status,
            content_type,
            len(body),
            sig,
            "HTML page without file signature",
        )

    return DocumentAccessResult(
        url, "invalid_content", status, content_type, len(body), sig, "Unrecognised content"
    )


def classify_document_url(
    url: str,
    client: httpx.Client,
    *,
    use_cache: bool = True,
) -> DocumentAccessResult:
    url = (url or "").strip()
    if not url:
        return DocumentAccessResult("", "missing", None, "", None, "", "Empty URL")

    if use_cache:
        cached = _load_cache(url)
        if cached:
            return cached

    headers = {"User-Agent": USER_AGENT, "Range": "bytes=0-2047"}
    status: int | None = None
    content_type = ""
    body = b""

    try:
        head = client.head(url, follow_redirects=True, timeout=30.0)
        status = head.status_code
        content_type = head.headers.get("content-type", "")
        if status == 405 or status == 501:
            raise httpx.HTTPStatusError("HEAD not supported", request=head.request, response=head)
    except Exception:
        try:
            response = client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            body = response.content[:2048]
        except Exception as exc:
            result = DocumentAccessResult(url, "missing", None, "", None, "", str(exc))
            _save_cache(result)
            return result
    else:
        if status == 200 and any(
            tok in content_type.lower() for tok in ("pdf", "spreadsheet", "octet-stream", "zip")
        ):
            try:
                response = client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
                body = response.content[:2048]
            except Exception:
                pass
        elif status == 200:
            try:
                response = client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
                body = response.content[:2048]
            except Exception:
                pass

    result = _classify_body(url, status, content_type, body)
    _save_cache(result)
    time.sleep(REQUEST_DELAY_SECONDS)
    return result


def _is_relevant_url(url: str) -> bool:
    u = url.lower()
    if not u.startswith("http"):
        return False
    if any(skip in u for skip in ("w3.org", "europa.eu/resource/schema", "xmlns")):
        return False
    if "mercell.com" in u or "permalink.mercell" in u:
        return True
    if "ted.europa.eu" in u and ("/detail/" in u or "/pdfs" in u or "/pdf" in u):
        return True
    if "doffin" in u:
        return True
    return False


def _normalize_fetch_urls(urls: list[str]) -> list[str]:
    """Deduplicate and keep one representative URL per notice/file."""
    kept: list[str] = []
    seen_notices: set[str] = set()
    seen_files: set[str] = set()

    for url in urls:
        if not _is_relevant_url(url):
            continue
        u = url.lower()
        if "mercell.com" in u:
            key = u.split("id=")[-1] if "id=" in u else u
            if key in seen_files:
                continue
            seen_files.add(key)
            kept.append(url)
            continue
        if "/detail/" in u:
            notice_id = u.split("/detail/")[-1].split("?")[0]
            if notice_id in seen_notices:
                continue
            seen_notices.add(notice_id)
            kept.append(url)
            continue
        if "/pdfs" in u or u.endswith("/pdf"):
            if "eng" not in u and "/en/" not in u:
                continue
            notice_key = u.rsplit("/", 2)[0]
            if notice_key in seen_files:
                continue
            seen_files.add(notice_key)
            kept.append(url)
    return kept


def classify_urls(urls: list[str], *, use_cache: bool = True) -> list[DocumentAccessResult]:
    unique = _normalize_fetch_urls(urls)

    results: list[DocumentAccessResult] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        for url in unique:
            results.append(classify_document_url(url, client, use_cache=use_cache))
    return results
