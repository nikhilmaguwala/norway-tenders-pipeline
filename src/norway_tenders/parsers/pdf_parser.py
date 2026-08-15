from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_pdf_text(path: Path) -> str | None:
    """Extract embedded text from PDF; return None if image-only or unreadable."""
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
        full = "\n".join(parts).strip()
        if len(full) < 20:
            logger.warning("PDF appears image-only or empty: %s", path.name)
            return None
        return full
    except Exception as exc:
        logger.warning("Failed to parse PDF %s: %s", path.name, exc)
        return None
