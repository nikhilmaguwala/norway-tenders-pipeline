from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image
import numpy as np

from norway_tenders.analytics.chart_helpers import (
    PRICING_UNAVAILABLE_REASONS,
    READINESS_CMAP,
    READINESS_LEVELS,
    READINESS_NORM,
    build_canonical_readiness_matrix,
    classify_strength_volume,
    hhi_display_value,
    paliperidone_supplier_stats,
    readiness_category_matrix,
    readiness_rgba_for_label,
    readiness_text_color,
    strength_bar_value,
)
from norway_tenders.analytics.metrics import (
    dedupe_estimated_by_notice,
    load_analytics_inputs,
    parse_volume,
)
from norway_tenders.analytics.run import run_analytics
from norway_tenders.analysis import analyse
from norway_tenders.settings import (
    ANALYTICS_NOTES_MD,
    ANALYTICS_SUMMARY_JSON,
    CHARTS_DIR,
    OUTPUT_CSV,
    TABLES_DIR,
)

EXPECTED_OUTPUT_SHA = "987dec4782b877f2f5a0ecf7e90b3fff3b0eab402934361a4e45c6f2078ccc40"

REQUIRED_TABLES = [
    "molecule_kpis.csv",
    "supplier_concentration.csv",
    "strength_demand.csv",
    "pricing_scenarios.csv",
    "opportunity_scorecard.csv",
]

REQUIRED_CHARTS = [
    "01_opportunity_priority",
    "02_strength_demand_heatmap",
    "03_supplier_concentration",
    "04_pricing_scenarios",
    "05_tender_readiness",
]


@pytest.fixture(scope="module")
def analytics_artifacts() -> dict[str, Path]:
    before = hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest()
    result = run_analytics()
    after = hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest()
    return {
        "before_sha": Path(before),  # store as path hack - use dict
        "before_sha_str": before,
        "after_sha_str": after,
        "summary": result.summary_path,
        "notes": result.notes_path,
    }


def test_output_csv_sha_unchanged_by_analyse() -> None:
    before = hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest()
    analyse()
    after = hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest()
    assert before == after == EXPECTED_OUTPUT_SHA


def test_five_target_molecules_in_kpis() -> None:
    kpis = pd.read_csv(TABLES_DIR / "molecule_kpis.csv")
    assert set(kpis["molecule"]) == {
        "Axitinib", "Everolimus", "Lenalidomide", "Anagrelide", "Paliperidone",
    }


def test_estimated_value_deduplicated_by_notice() -> None:
    inputs = load_analytics_inputs()
    estimates = dedupe_estimated_by_notice(inputs.output)
    assert estimates["300984-2021"] == 320000000.0
    assert estimates["244859-2024"] == 10000000.0
    kpis = pd.read_csv(TABLES_DIR / "molecule_kpis.csv")
    lena = kpis[kpis["molecule"] == "Lenalidomide"].iloc[0]
    assert float(lena["dedicatedEstimatedValue"]) == 320000000.0


def test_umbrella_values_excluded_from_molecule_estimates() -> None:
    kpis = pd.read_csv(TABLES_DIR / "molecule_kpis.csv")
    ax = kpis[kpis["molecule"] == "Axitinib"].iloc[0]
    ever = kpis[kpis["molecule"] == "Everolimus"].iloc[0]
    assert pd.isna(ax["dedicatedEstimatedValue"]) or ax["dedicatedEstimatedValue"] == ""
    assert pd.isna(ever["dedicatedEstimatedValue"]) or ever["dedicatedEstimatedValue"] == ""


def test_explicit_zero_volume_preserved() -> None:
    inputs = load_analytics_inputs()
    zero_rows = inputs.output[inputs.output["packsSoldLast12m"].apply(parse_volume) == 0]
    assert not zero_rows.empty
    assert (zero_rows["itemNumber"] == "362150").any()


def test_missing_volume_remains_missing() -> None:
    inputs = load_analytics_inputs()
    missing = inputs.output["packsSoldLast12m"].isna() | (inputs.output["packsSoldLast12m"] == "")
    assert missing.sum() > 0


def test_no_divide_by_zero_in_supplier_shares() -> None:
    supplier = pd.read_csv(TABLES_DIR / "supplier_concentration.csv")
    details = supplier[supplier["recordType"] == "supplier_detail"]
    for val in details["observedVolumeShare"]:
        assert 0 <= float(val) <= 1
    summaries = supplier[supplier["recordType"] == "molecule_summary"]
    for hhi in summaries["HHI"]:
        if hhi != "" and pd.notna(hhi):
            assert float(hhi) >= 0


def test_supplier_placeholders_not_in_output() -> None:
    text = OUTPUT_CSV.read_text(encoding="utf-8")
    assert "Grossister vgr 6" not in text
    assert "#N/A" not in text


def test_axitinib_evidence_confidence_lower_than_lenalidomide() -> None:
    scorecard = pd.read_csv(TABLES_DIR / "opportunity_scorecard.csv")
    ax = scorecard[
        (scorecard["molecule"] == "Axitinib")
        & (scorecard["component"] == "evidenceConfidenceScore")
    ]["normalizedComponentScore"].astype(float).iloc[0]
    lena = scorecard[
        (scorecard["molecule"] == "Lenalidomide")
        & (scorecard["component"] == "evidenceConfidenceScore")
    ]["normalizedComponentScore"].astype(float).iloc[0]
    assert ax < lena
    kpis = pd.read_csv(TABLES_DIR / "molecule_kpis.csv")
    assert "ATC" in kpis[kpis["molecule"] == "Axitinib"].iloc[0]["evidenceConfidence"]


def test_pricing_scenarios_require_price_and_volume() -> None:
    pricing = pd.read_csv(TABLES_DIR / "pricing_scenarios.csv")
    pack_rows = pricing[pricing["recordType"] == "pack_row"]
    inputs = load_analytics_inputs()
    for _, row in pack_rows.iterrows():
        item = str(row["itemNumber"]).replace(".0", "")
        notice = str(row["noticeId"])
        match = inputs.output[
            (inputs.output["itemNumber"].astype(str).str.replace(".0", "", regex=False) == item)
            & (inputs.output["noticeId"] == notice)
        ]
        assert not match.empty
        src = match.iloc[0]
        assert pd.notna(src["maxPrice"])
        assert parse_volume(src["packsSoldLast12m"]) is not None


def test_dmp_warnings_attached_in_pricing() -> None:
    pricing = pd.read_csv(TABLES_DIR / "pricing_scenarios.csv")
    axitinib = pricing[
        (pricing["molecule"] == "Axitinib") & (pricing["recordType"] == "pack_row")
    ]
    assert not axitinib.empty
    warnings = axitinib["warning"].fillna("").astype(str)
    assert warnings.str.contains("DMP|current-reference|Mixed", case=False, regex=True).any()


def test_score_between_zero_and_hundred() -> None:
    scorecard = pd.read_csv(TABLES_DIR / "opportunity_scorecard.csv")
    for col in ("normalizedComponentScore", "opportunityPriorityScore"):
        vals = scorecard[col].astype(float)
        assert vals.min() >= 0
        assert vals.max() <= 100


def test_component_contributions_sum_to_total_score() -> None:
    scorecard = pd.read_csv(TABLES_DIR / "opportunity_scorecard.csv")
    for molecule in scorecard["molecule"].unique():
        mol = scorecard[scorecard["molecule"] == molecule]
        total = float(mol["opportunityPriorityScore"].iloc[0])
        contrib = float(mol["weightedContribution"].sum())
        assert abs(total - contrib) < 0.15


def test_deterministic_rankings() -> None:
    first = pd.read_csv(TABLES_DIR / "opportunity_scorecard.csv")
    run_analytics()
    second = pd.read_csv(TABLES_DIR / "opportunity_scorecard.csv")
    first_rank = (
        first.drop_duplicates("molecule")
        .sort_values(["opportunityPriorityScore", "molecule"], ascending=[False, True])["molecule"]
        .tolist()
    )
    second_rank = (
        second.drop_duplicates("molecule")
        .sort_values(["opportunityPriorityScore", "molecule"], ascending=[False, True])["molecule"]
        .tolist()
    )
    assert first_rank == second_rank


def test_required_artifacts_exist() -> None:
    for name in REQUIRED_TABLES:
        assert (TABLES_DIR / name).exists()
    assert ANALYTICS_SUMMARY_JSON.exists()
    assert ANALYTICS_NOTES_MD.exists()
    for stem in REQUIRED_CHARTS:
        assert (CHARTS_DIR / f"{stem}.png").exists()
        assert (CHARTS_DIR / f"{stem}.svg").exists()


def test_png_minimum_dimensions() -> None:
    for stem in REQUIRED_CHARTS:
        with Image.open(CHARTS_DIR / f"{stem}.png") as img:
            width, height = img.size
            assert width >= 1600, stem
            assert height >= 900, stem


def test_analyse_twice_produces_identical_table_and_summary_hashes() -> None:
    run_analytics()
    table_hashes = {
        name: hashlib.sha256((TABLES_DIR / name).read_bytes()).hexdigest()
        for name in REQUIRED_TABLES
    }
    summary_hash = hashlib.sha256(ANALYTICS_SUMMARY_JSON.read_bytes()).hexdigest()
    notes_hash = hashlib.sha256(ANALYTICS_NOTES_MD.read_bytes()).hexdigest()
    chart_hashes = {
        stem: hashlib.sha256((CHARTS_DIR / f"{stem}.png").read_bytes()).hexdigest()
        for stem in REQUIRED_CHARTS
    }
    run_analytics()
    for name in REQUIRED_TABLES:
        assert hashlib.sha256((TABLES_DIR / name).read_bytes()).hexdigest() == table_hashes[name]
    assert hashlib.sha256(ANALYTICS_SUMMARY_JSON.read_bytes()).hexdigest() == summary_hash
    assert hashlib.sha256(ANALYTICS_NOTES_MD.read_bytes()).hexdigest() == notes_hash
    for stem in REQUIRED_CHARTS:
        assert hashlib.sha256((CHARTS_DIR / f"{stem}.png").read_bytes()).hexdigest() == chart_hashes[stem]


def test_summary_contains_recommendations() -> None:
    summary = json.loads(ANALYTICS_SUMMARY_JSON.read_text(encoding="utf-8"))
    assert "primaryOpportunity" in summary["recommendations"]
    assert "secondaryOpportunity" in summary["recommendations"]
    assert summary["rowCount"] == 41


def test_readiness_colour_mapping_matches_labels() -> None:
    matrix = build_canonical_readiness_matrix(pd.read_csv(TABLES_DIR / "readiness_matrix.csv"))
    numeric = readiness_category_matrix(matrix)
    assert numeric.min() == 0
    assert numeric.max() == 2
    for row_index, molecule in enumerate(matrix.index):
        for col_index, dimension in enumerate(matrix.columns):
            label = str(matrix.iloc[row_index, col_index])
            assert numeric[row_index, col_index] == READINESS_LEVELS[label]
            assert np.allclose(
                readiness_rgba_for_label(label)[:3],
                READINESS_CMAP(READINESS_NORM(READINESS_LEVELS[label]))[:3],
                atol=0.01,
            )
            assert readiness_text_color(label) == ("white" if label == "Strong" else "black")


def test_missing_strength_volume_not_treated_as_zero() -> None:
    assert classify_strength_volume("") == "unavailable"
    assert classify_strength_volume(None) == "unavailable"
    assert strength_bar_value("") is None
    assert strength_bar_value(0) == 0.0
    assert strength_bar_value(0.0) == 0.0
    strength = pd.read_csv(TABLES_DIR / "strength_demand.csv")
    for _, row in strength.iterrows():
        mode = classify_strength_volume(row["observedVolume"])
        if mode == "unavailable":
            assert strength_bar_value(row["observedVolume"]) is None
        elif mode == "zero":
            assert strength_bar_value(row["observedVolume"]) == 0.0


def test_hhi_unavailable_when_coverage_zero() -> None:
    supplier = pd.read_csv(TABLES_DIR / "supplier_concentration.csv")
    summaries = supplier[supplier["recordType"] == "molecule_summary"]
    for _, row in summaries.iterrows():
        coverage = float(row["concentrationCoveragePct"]) if row["concentrationCoveragePct"] != "" else 0.0
        display = hhi_display_value(row["HHI"], row["concentrationCoveragePct"])
        if coverage <= 0:
            assert display == "Unavailable"


def test_paliperidone_supplier_counts_and_coverage() -> None:
    supplier = pd.read_csv(TABLES_DIR / "supplier_concentration.csv")
    stats = paliperidone_supplier_stats(supplier)
    assert stats["listedSuppliers"] == 4
    assert stats["positiveVolumeSuppliers"] == 2
    assert stats["zeroVolumeListedSuppliers"] == 2
    assert stats["excludedVolume"] == 50.0
    assert round(stats["concentrationCoveragePct"], 1) == 99.4
    assert round(stats["hhi"], 2) == 0.51


def test_pricing_unavailability_reasons_for_three_molecules() -> None:
    assert set(PRICING_UNAVAILABLE_REASONS) == {"Lenalidomide", "Anagrelide", "Everolimus"}
    summary = json.loads(ANALYTICS_SUMMARY_JSON.read_text(encoding="utf-8"))
    assert summary["pricingUnavailableReasons"] == PRICING_UNAVAILABLE_REASONS
    notes = ANALYTICS_NOTES_MD.read_text(encoding="utf-8")
    assert "How to read unavailable data" in notes
    assert "four listed suppliers" in notes.lower()


def test_analytics_notes_paliperidone_wording() -> None:
    summary = json.loads(ANALYTICS_SUMMARY_JSON.read_text(encoding="utf-8"))
    narrative = summary["paliperidoneSupplierNarrative"]
    assert "four listed suppliers" in narrative
    assert "positive observed volume" in narrative
    assert "explicit zero observed volume" in narrative
    for forbidden in ("active competitors", "winners", "market-share holders"):
        assert forbidden not in narrative.lower()
