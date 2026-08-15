from __future__ import annotations

from datetime import date

EXTRACTION_AS_OF_DATE = date(2026, 8, 14)

MOLECULE_ORDER = (
    "Anagrelide",
    "Axitinib",
    "Everolimus",
    "Lenalidomide",
    "Paliperidone",
)

READINESS_DIMENSION_ORDER = (
    "molecule confirmation",
    "current/open timing",
    "volume coverage",
    "price coverage",
    "supplier coverage",
    "dedicated estimate availability",
    "award evidence availability",
)

SCORE_WEIGHTS = {
    "observableScaleScore": 0.30,
    "contestabilityScore": 0.25,
    "portfolioBreadthScore": 0.20,
    "timingActionabilityScore": 0.15,
    "evidenceConfidenceScore": 0.10,
}

DISCOUNT_PCTS = (0.05, 0.10, 0.20, 0.30, 0.40)

PRIORITY_BAND_HIGH = 60.0
PRIORITY_BAND_MEDIUM = 40.0

CHART_DPI = 100
CHART_FIGSIZE = (16, 9)

SOURCE_COVERAGE_STATEMENT = (
    "Analytics cover five target molecules drawn from six canonical Norwegian hospital "
    "pharmaceutical procedures in a small take-home dataset. Results describe observed "
    "source documents only and do not represent full Norwegian market coverage, market "
    "share, win probability, or optimal bid pricing."
)

PALETTE = {
    "Anagrelide": "#4C78A8",
    "Axitinib": "#F58518",
    "Everolimus": "#54A24B",
    "Lenalidomide": "#E45756",
    "Paliperidone": "#B279A2",
    "High": "#2A9D8F",
    "Medium": "#E9C46A",
    "Low": "#E76F51",
    "unavailable": "#CCCCCC",
    "excluded": "#999999",
}
