from __future__ import annotations

from pathlib import Path

from norway_tenders.analytics.run import AnalyticsResult, run_analytics
from norway_tenders.settings import OUTPUT_CSV


def analyse(output_path: Path | None = None) -> AnalyticsResult:
    """Run Phase 6 decision-oriented analytics (offline; does not modify output.csv)."""
    return run_analytics(output_path=output_path or OUTPUT_CSV)
