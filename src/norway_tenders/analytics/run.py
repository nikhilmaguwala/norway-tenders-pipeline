from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from norway_tenders.analytics.charts import (
    build_readiness_wide,
    chart_opportunity_priority,
    chart_pricing_scenarios,
    chart_strength_demand,
    chart_supplier_concentration,
    chart_tender_readiness,
    write_readiness_matrix_csv,
)
from norway_tenders.analytics.chart_helpers import (
    PALIPERIDONE_SUPPLIER_NARRATIVE,
    PRICING_UNAVAILABLE_REASONS,
    paliperidone_supplier_stats,
)
from norway_tenders.analytics.constants import (
    EXTRACTION_AS_OF_DATE,
    SCORE_WEIGHTS,
    SOURCE_COVERAGE_STATEMENT,
)
from norway_tenders.analytics.metrics import AnalyticsInputs, load_analytics_inputs, merge_evidence
from norway_tenders.analytics.render_qa import reset_chart_qa, write_chart_qa_md
from norway_tenders.analytics.scoring import build_opportunity_scorecard, derive_recommendations
from norway_tenders.analytics.tables import (
    build_molecule_kpis,
    build_pricing_scenarios,
    build_strength_demand,
    build_supplier_concentration,
)
from norway_tenders.settings import (
    ANALYTICS_NOTES_MD,
    ANALYTICS_SUMMARY_JSON,
    CHARTS_DIR,
    CHART_QA_MD,
    OUTPUT_CSV,
    PROCESSED_DIR,
    PROJECT_ROOT,
    SEEDS_DIR,
    TABLES_DIR,
)


@dataclass
class AnalyticsResult:
    summary_path: Path
    notes_path: Path
    chart_paths: list[Path]
    table_paths: list[Path]


def run_analytics(*, output_path: Path = OUTPUT_CSV) -> AnalyticsResult:
    inputs = load_analytics_inputs(output_path=output_path)
    enriched = merge_evidence(inputs)

    kpis = build_molecule_kpis(inputs)
    supplier = build_supplier_concentration(inputs)
    strength = build_strength_demand(inputs)
    pricing = build_pricing_scenarios(inputs, enriched)
    scorecard, ranking = build_opportunity_scorecard(inputs)
    recommendations = derive_recommendations(ranking)
    readiness_wide = build_readiness_wide(inputs, kpis, ranking)

    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    reset_chart_qa()

    table_paths = [
        _write_csv(kpis, TABLES_DIR / "molecule_kpis.csv"),
        _write_csv(supplier, TABLES_DIR / "supplier_concentration.csv"),
        _write_csv(strength, TABLES_DIR / "strength_demand.csv"),
        _write_csv(pricing, TABLES_DIR / "pricing_scenarios.csv"),
        _write_csv(scorecard, TABLES_DIR / "opportunity_scorecard.csv"),
        write_readiness_matrix_csv(readiness_wide),
    ]

    chart_paths: list[Path] = []
    chart_paths.extend(chart_opportunity_priority(ranking, scorecard, kpis))
    chart_paths.extend(chart_strength_demand(strength))
    chart_paths.extend(chart_supplier_concentration(supplier))
    chart_paths.extend(chart_pricing_scenarios(pricing))
    chart_paths.extend(chart_tender_readiness(readiness_wide))

    write_chart_qa_md(CHART_QA_MD)

    summary = _build_summary(
        inputs, kpis, ranking, scorecard, recommendations, chart_paths, table_paths, supplier,
    )
    ANALYTICS_SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
    ANALYTICS_SUMMARY_JSON.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    ANALYTICS_NOTES_MD.write_text(_build_notes(summary, ranking, recommendations), encoding="utf-8")

    return AnalyticsResult(
        summary_path=ANALYTICS_SUMMARY_JSON,
        notes_path=ANALYTICS_NOTES_MD,
        chart_paths=chart_paths,
        table_paths=table_paths,
    )


def _write_csv(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False, encoding="utf-8", na_rep="")
    return path


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def _build_summary(
    inputs: AnalyticsInputs,
    kpis: pd.DataFrame,
    ranking: pd.DataFrame,
    scorecard: pd.DataFrame,
    recommendations: dict[str, Any],
    chart_paths: list[Path],
    table_paths: list[Path],
    supplier_df: pd.DataFrame,
) -> dict[str, Any]:
    procedures = inputs.output[["noticeId", "tenderRef", "productMolecule"]].drop_duplicates()
    pali_stats = paliperidone_supplier_stats(supplier_df)
    return {
        "extractionAsOfDate": EXTRACTION_AS_OF_DATE.isoformat(),
        "sourceCoverageStatement": SOURCE_COVERAGE_STATEMENT,
        "rowCount": int(len(inputs.output)),
        "procedureCount": int(len(procedures)),
        "moleculeRowCounts": inputs.output["productMolecule"].value_counts().to_dict(),
        "kpiRecords": kpis.to_dict(orient="records"),
        "opportunityRanking": ranking.to_dict(orient="records"),
        "scorecardComponents": scorecard.to_dict(orient="records"),
        "scoringFormula": {
            "description": "Transparent prioritisation heuristic (0–100), not win probability.",
            "componentWeights": SCORE_WEIGHTS,
        },
        "recommendations": recommendations,
        "moleculeEvidence": {
            row["molecule"]: {
                "evidenceConfidence": row["evidenceConfidence"],
                "importantLimitation": row["importantLimitation"],
                "dedicatedEstimatedValue": row["dedicatedEstimatedValue"],
                "observedVolume": row["observedVolume"],
            }
            for row in kpis.to_dict(orient="records")
        },
        "paliperidoneSupplierNarrative": PALIPERIDONE_SUPPLIER_NARRATIVE,
        "paliperidoneSupplierStats": pali_stats,
        "pricingUnavailableReasons": PRICING_UNAVAILABLE_REASONS,
        "howToReadUnavailableData": [
            "Missing values are not zero — they indicate absent evidence in collected rows.",
            "Explicit zero is an observed zero (e.g. packsSoldLast12m = 0 on a price-schedule row).",
            "Unavailable metrics are not evidence of no demand, no supplier, or no opportunity.",
        ],
        "majorLimitations": _major_limitations(kpis),
        "chartPaths": [_repo_relative(p) for p in chart_paths],
        "tablePaths": [_repo_relative(p) for p in table_paths],
        "sourceArtifactPaths": {
            "outputCsv": _repo_relative(OUTPUT_CSV),
            "packEvidence": _repo_relative(PROCESSED_DIR / "pack_evidence.csv"),
            "noticeValueAudit": _repo_relative(PROCESSED_DIR / "notice_value_audit.csv"),
            "awardMetadataAudit": _repo_relative(PROCESSED_DIR / "award_metadata_audit.csv"),
            "dmpPriceJoinAudit": _repo_relative(PROCESSED_DIR / "dmp_price_join_audit.csv"),
            "semanticCleanupAudit": _repo_relative(PROCESSED_DIR / "final_semantic_cleanup_audit.csv"),
            "lifecycleLinkage": _repo_relative(PROCESSED_DIR / "lifecycle_linkage.csv"),
            "sourcesCsv": _repo_relative(SEEDS_DIR / "sources.csv"),
        },
        "uncalculatedAnalytics": _uncalculated(kpis, inputs),
    }


def _major_limitations(kpis: pd.DataFrame) -> list[str]:
    items = [
        SOURCE_COVERAGE_STATEMENT,
        "Award fields remain unverified and are excluded from scoring inputs.",
        "Observed source pack volume uses mixed period definitions (calendar-year proxy vs last-12-months).",
        "DMP-enriched maxPrice values dated 2026-08-03 are current references, not tender-time prices.",
        "Listed suppliers in price schedules are not award winners.",
        PALIPERIDONE_SUPPLIER_NARRATIVE,
        (
            "Paliperidone HHI 0.51 is calculated using positive observed volume shares only; "
            "50 packs from the older LIS 2301d procedure are excluded from supplier concentration "
            "because supplier is blank; concentration coverage is 99.4%."
        ),
    ]
    for row in kpis.itertuples(index=False):
        if row.importantLimitation:
            items.append(f"{row.molecule}: {row.importantLimitation}")
    return items


def _uncalculated(kpis: pd.DataFrame, inputs: AnalyticsInputs) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for row in kpis.itertuples(index=False):
        if row.observedVolume == "":
            items.append({
                "molecule": row.molecule,
                "metric": "observedVolume",
                "reason": "No non-missing packsSoldLast12m values in collected prisskjema rows.",
            })
        if row.dedicatedEstimatedValue == "":
            items.append({
                "molecule": row.molecule,
                "metric": "dedicatedEstimatedValue",
                "reason": "No accepted dedicated notice estimate mapped for this molecule.",
            })
    if inputs.award_metadata_audit.empty:
        items.append({
            "molecule": "all",
            "metric": "awardAnalytics",
            "reason": "Award metadata audit contains no accepted concluded awards.",
        })
    return items


def _build_notes(
    summary: dict[str, Any],
    ranking: pd.DataFrame,
    recommendations: dict[str, Any],
) -> str:
    primary = recommendations["primaryOpportunity"]
    secondary = recommendations["secondaryOpportunity"]
    watchlist = ", ".join(recommendations["watchlistOpportunities"])
    ranking_lines = "\n".join(
        f"- **{row['molecule']}**: {row['opportunityPriorityScore']} ({row['priorityBand']})"
        for _, row in ranking.iterrows()
    )
    return f"""# Analytics notes (Phase 6)

## Coverage statement

{summary['sourceCoverageStatement']}

Extraction as-of date: **{summary['extractionAsOfDate']}**.

## What each chart answers

1. **01_opportunity_priority** — Which molecules combine observable scale with contestability for prioritisation (heuristic, not win probability).
2. **02_strength_demand_heatmap** — Which strengths drive observed source pack volume within each molecule (grouped bars; filename retained for compatibility).
3. **03_supplier_concentration** — How observed source volume is distributed across listed suppliers (not market share).
4. **04_pricing_scenarios** — Reference gross values at maximum AIP and simple discount scenarios (not bid recommendations).
5. **05_tender_readiness** — Evidence readiness across confirmation, timing, volume, price, supplier, estimate, and award dimensions.

## Calculation definitions

- **Observed source pack volume**: Sum of `packsSoldLast12m` where present; explicit zero retained; missing left out of sums.
- **Dedicated estimated value**: Notice-level `estimatedValue` deduplicated by `noticeId`, including only audit-accepted dedicated estimates.
- **Reference gross value**: `maxPrice × observedVolume` on rows where both exist.
- **HHI**: Sum of squared observed-volume shares across listed suppliers with nonblank supplier and volume.
- **Opportunity Priority Score**: Weighted sum of five normalised component scores (0–100 each).

Component weights: {json.dumps(SCORE_WEIGHTS)}.

## Key distinctions

| Concept | Meaning in this dataset |
|---|---|
| Listed supplier | Supplier name appearing in a price schedule row — not necessarily the tender winner |
| Maximum AIP (`maxPrice`) | Official per-pack ceiling/reference — not achieved tender price |
| Observed source volume | Documented pack counts from prisskjema — not total market size |
| Estimated contract value | Notice-level procurement estimate — not pack-level revenue |

## Notice value handling

- `estimatedValue` is notice metadata and must be deduplicated by `noticeId` before aggregation.
- Umbrella and multi-molecule notice totals (e.g. LIS 2207 oncology NOK 3.2bn, Everolimus+mycophenolic acid NOK 128m) are rejected and not allocated to molecule rows.
- Historical kravspesifikasjon turnover is not substituted for notice estimates.

## Missing vs explicit zero

- Missing `packsSoldLast12m` is excluded from volume sums and share calculations.
- Explicit `packsSoldLast12m = 0` is a genuine observed zero and is retained.

## How to read unavailable data

- **Missing is not zero** — grey/hatched chart markers and blank table cells indicate absent evidence, not zero demand.
- **Explicit zero is observed zero** — labelled as "0 observed packs" where volume was documented as zero.
- **Unavailable metrics** (e.g. HHI when concentration coverage is 0%, pricing scenarios without both price and volume) are not evidence of no demand, no supplier, or no opportunity.

## Paliperidone supplier concentration

{PALIPERIDONE_SUPPLIER_NARRATIVE}

- Four listed suppliers in collected price schedules; two with positive observed volume (Amdipharm, Janssen-Cilag).
- Two listed suppliers with explicit zero observed volume (Orifarm, Zentiva).
- HHI 0.51 uses positive observed volume shares only.
- 50 packs from LIS 2301d excluded because supplier is blank.
- Supplier-volume concentration coverage: 99.4%.

Do not describe the four listed suppliers as active competitors, winners, market participants, or market-share holders based solely on this dataset.

## DMP price warning

Maximum AIP values enriched from DMP effective **2026-08-03** provide a current reference for gap-filling where tender documents lack prices. They must not be interpreted as historical tender-time prices for closed procedures.

## Axitinib evidence confidence

Axitinib rows are confirmed by ATC **L01EK01** and **Inlyta** brand without explicit INN in pack rows, so evidence confidence is lower than name+ATC molecules.

## Score interpretation

The Opportunity Priority Score is a **transparent prioritisation heuristic** for qualification and evidence gathering. It is **not** win probability, market share, or an optimal bid.

## Opportunity ranking

{ranking_lines}

## Data-driven recommendation

- **Primary opportunity**: {primary}
- **Secondary opportunity**: {secondary}
- **Watchlist / evidence-gap candidates**: {watchlist}

### Next evidence before bid/no-bid

Use the `recommendedNextAction` field in `opportunity_scorecard.csv` per molecule. Awards remain outside reliable coverage; do not treat listed suppliers as winners.
"""
