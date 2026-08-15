from __future__ import annotations

import re
from typing import Any


def normalize_strength_display(raw: str) -> str:
    """Format strength for display, e.g. '20mg' -> '20 mg', '7.5mg' -> '7.5 mg'."""
    text = (raw or "").strip()
    if not text:
        return ""
    match = re.match(
        r"^(\d+(?:[.,]\d+)?)\s*(mg|g|mcg|µg|ug|ml|%)\b",
        text,
        re.IGNORECASE,
    )
    if match:
        value = match.group(1).replace(",", ".")
        unit = match.group(2).lower()
        if unit in {"µg", "ug"}:
            unit = "mcg"
        return f"{value} {unit}"
    return text


def normalize_pack_volume(value: Any) -> int | float | None:
    """Return pack counts; preserve explicit zero vs blank/unknown."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text in {"", "-", "#N/A", "N/A"}:
            return None
        if text in {"0", "0.0", "0,0"}:
            return 0
        try:
            parsed = float(text.replace(",", "."))
        except ValueError:
            return None
        return int(parsed) if parsed == int(parsed) else parsed
    if isinstance(value, (int, float)):
        if value == 0:
            return 0
        if isinstance(value, float) and value == int(value):
            return int(value)
        return value
    try:
        parsed = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return int(parsed) if parsed == int(parsed) else parsed


def parse_pack_volume(value: Any) -> tuple[int | float | None, str]:
    """Parse volume with optional malformed-text warning."""
    if value is None:
        return None, ""
    if isinstance(value, str):
        text = value.strip()
        if text in {"", "-", "#N/A", "N/A"}:
            return None, ""
        if text in {"0", "0.0", "0,0"}:
            return 0, ""
        try:
            float(text.replace(",", "."))
        except ValueError:
            return None, "malformed_volume_text"
    result = normalize_pack_volume(value)
    return result, ""
