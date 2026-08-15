from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from norway_tenders.analytics.chart_helpers import (
    READINESS_CMAP,
    READINESS_DIMENSION_ORDER,
    READINESS_LEVELS,
    READINESS_NORM,
    build_canonical_readiness_matrix,
    readiness_cell_audit,
    readiness_rgba_for_label,
)
from norway_tenders.analytics.charts import build_readiness_table, build_readiness_wide
from norway_tenders.analytics.constants import MOLECULE_ORDER
from norway_tenders.analytics.metrics import load_analytics_inputs
from norway_tenders.analytics.run import run_analytics
from norway_tenders.settings import (
    CHART_QA_MD,
    CHARTS_DIR,
    OUTPUT_CSV,
    TABLES_DIR,
)

EXPECTED_OUTPUT_SHA = "987dec4782b877f2f5a0ecf7e90b3fff3b0eab402934361a4e45c6f2078ccc40"

REQUIRED_CHARTS = [
    "01_opportunity_priority",
    "02_strength_demand_heatmap",
    "03_supplier_concentration",
    "04_pricing_scenarios",
    "05_tender_readiness",
]

EXPECTED_READINESS_CELLS = {
    ("Anagrelide", "volume coverage"): ("Missing", 0),
    ("Anagrelide", "dedicated estimate availability"): ("Strong", 2),
    ("Axitinib", "volume coverage"): ("Strong", 2),
    ("Axitinib", "price coverage"): ("Strong", 2),
    ("Axitinib", "dedicated estimate availability"): ("Missing", 0),
    ("Lenalidomide", "price coverage"): ("Missing", 0),
    ("Lenalidomide", "dedicated estimate availability"): ("Strong", 2),
    ("Paliperidone", "molecule confirmation"): ("Strong", 2),
    ("Paliperidone", "current/open timing"): ("Strong", 2),
}


@pytest.fixture(scope="module", autouse=True)
def _ensure_analytics() -> None:
    run_analytics()


def test_output_csv_sha_unchanged() -> None:
    assert hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest() == EXPECTED_OUTPUT_SHA


def test_readiness_matrix_csv_exists() -> None:
    path = TABLES_DIR / "readiness_matrix.csv"
    assert path.exists()
    df = pd.read_csv(path)
    assert list(df["molecule"]) == list(MOLECULE_ORDER)
    assert list(df.columns[1:]) == list(READINESS_DIMENSION_ORDER)


def test_readiness_every_cell_category_colour_label_agree() -> None:
    canonical = pd.read_csv(TABLES_DIR / "readiness_matrix.csv").pipe(build_canonical_readiness_matrix)
    audit = readiness_cell_audit(canonical)
    assert len(audit) == len(MOLECULE_ORDER) * len(READINESS_DIMENSION_ORDER)
    for cell in audit:
        label = cell["category"]
        numeric = cell["numericCategory"]
        assert READINESS_LEVELS[label] == numeric
        expected_rgba = readiness_rgba_for_label(label)
        assert np.allclose(cell["rgba"][:3], expected_rgba[:3], atol=0.01)
        assert cell["displayLabel"] == label
        rendered = canonical.iloc[cell["rowIndex"], cell["columnIndex"]]
        assert rendered == label


def test_readiness_specific_expected_cells() -> None:
    canonical = pd.read_csv(TABLES_DIR / "readiness_matrix.csv").pipe(build_canonical_readiness_matrix)
    for (molecule, dimension), (label, numeric) in EXPECTED_READINESS_CELLS.items():
        assert canonical.loc[molecule, dimension] == label
        assert READINESS_LEVELS[label] == numeric
        assert np.allclose(
            readiness_rgba_for_label(label)[:3],
            READINESS_CMAP(READINESS_NORM(numeric))[:3],
            atol=0.01,
        )
    for molecule in MOLECULE_ORDER:
        assert canonical.loc[molecule, "award evidence availability"] == "Missing"
        assert READINESS_LEVELS["Missing"] == 0


def test_scores_and_ranking_unchanged() -> None:
    scorecard = pd.read_csv(TABLES_DIR / "opportunity_scorecard.csv")
    ranking = (
        scorecard.drop_duplicates("molecule")
        .sort_values(["opportunityPriorityScore", "molecule"], ascending=[False, True])
    )
    assert ranking["molecule"].tolist() == [
        "Paliperidone",
        "Lenalidomide",
        "Axitinib",
        "Anagrelide",
        "Everolimus",
    ]
    assert ranking.iloc[0]["opportunityPriorityScore"] == pytest.approx(79.97, abs=0.01)


def test_chart_qa_md_generated() -> None:
    assert CHART_QA_MD.exists()
    text = CHART_QA_MD.read_text(encoding="utf-8")
    for stem in REQUIRED_CHARTS:
        assert stem in text
        assert "Clipping check: pass" in text
        assert "Overall: pass" in text


def test_png_minimum_dimensions() -> None:
    for stem in REQUIRED_CHARTS:
        with Image.open(CHARTS_DIR / f"{stem}.png") as img:
            width, height = img.size
            assert width >= 1600, stem
            assert height >= 900, stem


def test_readiness_wide_matches_long_table() -> None:
    inputs = load_analytics_inputs()
    kpis = pd.read_csv(TABLES_DIR / "molecule_kpis.csv")
    scorecard = pd.read_csv(TABLES_DIR / "opportunity_scorecard.csv")
    ranking = scorecard.drop_duplicates("molecule")[
        ["molecule", "opportunityPriorityScore", "priorityBand", "recommendedNextAction"]
    ]
    wide = build_readiness_wide(inputs, kpis, ranking)
    long = build_readiness_table(inputs, kpis, ranking)
    canonical = build_canonical_readiness_matrix(wide)
    for _, row in long.iterrows():
        assert canonical.loc[row["molecule"], row["dimension"]] == row["readiness"]
