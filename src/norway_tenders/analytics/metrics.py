from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from norway_tenders.settings import (
    OUTPUT_CSV,
    PROCESSED_DIR,
    SEEDS_DIR,
)


def parse_optional_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def parse_volume(value: Any) -> float | None:
    """Preserve explicit zero; treat blank as missing."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if text == "":
        return None
    if text in {"0", "0.0"}:
        return 0.0
    return parse_optional_float(text)


def is_missing(value: Any) -> bool:
    return parse_optional_float(value) is None and not (str(value).strip() in {"0", "0.0"})


def dedupe_estimated_by_notice(df: pd.DataFrame) -> dict[str, float]:
    """Deduplicate accepted notice-level estimatedValue by noticeId."""
    estimates: dict[str, float] = {}
    for notice_id, grp in df.groupby("noticeId"):
        values = {
            parse_optional_float(v)
            for v in grp["estimatedValue"].tolist()
            if parse_optional_float(v) is not None
        }
        if len(values) == 1:
            estimates[str(notice_id)] = values.pop()
    return estimates


def dedicated_estimate_for_molecule(
    df: pd.DataFrame,
    molecule: str,
    accepted_notices: set[str],
) -> float | None:
    mol_df = df[df["productMolecule"] == molecule]
    total = 0.0
    found = False
    for notice_id, amount in dedupe_estimated_by_notice(mol_df).items():
        if notice_id in accepted_notices:
            total += amount
            found = True
    return total if found else None


def calculate_hhi(shares: list[float]) -> float | None:
  if not shares:
      return None
  return round(sum(s * s for s in shares), 2)


def normalize_strength(strength: Any) -> str:
    text = str(strength or "").strip()
    if not text:
        return "Unknown strength"
    return re.sub(r"\s+", " ", text)


def clamp_score(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def format_nok(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value) >= 1_000_000:
        return f"NOK {value:,.0f}"
    return f"NOK {value:,.2f}"


@dataclass
class AnalyticsInputs:
    output: pd.DataFrame
    pack_evidence: pd.DataFrame
    notice_value_audit: pd.DataFrame
    award_metadata_audit: pd.DataFrame
    notice_metadata_audit: pd.DataFrame
    dmp_price_join_audit: pd.DataFrame
    semantic_cleanup_audit: pd.DataFrame
    lifecycle_linkage: pd.DataFrame
    row_filter_audit: pd.DataFrame
    phase5f_quality: dict[str, Any]
    sources: pd.DataFrame
    accepted_estimate_notices: set[str] = field(default_factory=set)

    @property
    def estimates_by_notice(self) -> dict[str, float]:
        return dedupe_estimated_by_notice(self.output)


def load_accepted_estimate_notices(audit: pd.DataFrame) -> set[str]:
    if audit.empty:
        return set()
    accepted = audit[
        (audit.get("mappingDecision", pd.Series(dtype=str)) == "accepted")
        & (audit.get("mappedField", pd.Series(dtype=str)) == "estimatedValue")
    ]
    return set(accepted["noticeId"].astype(str).tolist())


def load_analytics_inputs(
    *,
    output_path: Path = OUTPUT_CSV,
    processed_dir: Path = PROCESSED_DIR,
    seeds_dir: Path = SEEDS_DIR,
) -> AnalyticsInputs:
    output = pd.read_csv(output_path, dtype=str)
    for col in ("packSize", "maxPrice", "packsSoldLast12m", "estimatedValue", "awardedValue"):
        if col in output.columns:
            output[col] = output[col].apply(parse_optional_float)

    def _read(name: str) -> pd.DataFrame:
        path = processed_dir / name
        if path.exists():
            return pd.read_csv(path, dtype=str)
        return pd.DataFrame()

    notice_value_audit = _read("notice_value_audit.csv")
    phase5f_path = processed_dir / "phase5f_quality_report.json"
    phase5f_quality: dict[str, Any] = {}
    if phase5f_path.exists():
        import json

        phase5f_quality = json.loads(phase5f_path.read_text(encoding="utf-8"))

    sources_path = seeds_dir / "sources.csv"
    sources = pd.read_csv(sources_path, dtype=str) if sources_path.exists() else pd.DataFrame()

    accepted = load_accepted_estimate_notices(notice_value_audit)
    if not accepted and phase5f_quality.get("accepted_estimates_by_notice"):
        accepted = set(phase5f_quality["accepted_estimates_by_notice"].keys())

    return AnalyticsInputs(
        output=output,
        pack_evidence=_read("pack_evidence.csv"),
        notice_value_audit=notice_value_audit,
        award_metadata_audit=_read("award_metadata_audit.csv"),
        notice_metadata_audit=_read("notice_metadata_audit.csv"),
        dmp_price_join_audit=_read("dmp_price_join_audit.csv"),
        semantic_cleanup_audit=_read("final_semantic_cleanup_audit.csv"),
        lifecycle_linkage=_read("lifecycle_linkage.csv"),
        row_filter_audit=_read("row_filter_audit.csv"),
        phase5f_quality=phase5f_quality,
        sources=sources,
        accepted_estimate_notices=accepted,
    )


def merge_evidence(inputs: AnalyticsInputs) -> pd.DataFrame:
    df = inputs.output.copy()
    if inputs.pack_evidence.empty:
        return df

    evidence = inputs.pack_evidence.copy()
    evidence["sourceDocument"] = evidence["localFile"].apply(lambda p: Path(str(p)).name)
    merge_cols = [
        "sourceDocument", "itemNumber", "maxPriceSource", "maxPriceEffectiveDate",
        "dmpTemporalWarning", "volumePeriodLabel", "volumeIsTwelveMonths",
        "supplierExportDecision", "rawNoticeType", "rawProcedureType",
    ]
    available = [c for c in merge_cols if c in evidence.columns]
    merged = df.merge(
        evidence[available],
        on=["sourceDocument", "itemNumber"],
        how="left",
        suffixes=("", "_ev"),
    )
    return merged
