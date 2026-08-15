from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap

from norway_tenders.analytics.constants import (
    MOLECULE_ORDER,
    READINESS_DIMENSION_ORDER,
)

READINESS_LEVELS: dict[str, int] = {"Missing": 0, "Partial": 1, "Strong": 2}
READINESS_LEVEL_NAMES: tuple[str, ...] = ("Missing", "Partial", "Strong")
READINESS_CMAP = ListedColormap(["#D9D9D9", "#F4D35E", "#1B7F3A"])
READINESS_NORM = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], READINESS_CMAP.N)

PRICING_UNAVAILABLE_REASONS: dict[str, str] = {
    "Lenalidomide": "Observed volume available, but no maxPrice in collected prisskjema rows.",
    "Anagrelide": "Partial price coverage, but no observed volume in collected prisskjema rows.",
    "Everolimus": "maxPrice available, but no observed volume in collected prisskjema rows.",
}

SCENARIO_LABELS: dict[str, str] = {
    "reference_at_max_aip": "Reference at max AIP",
    "discount_5pct": "5% below max AIP",
    "discount_10pct": "10% below max AIP",
    "discount_20pct": "20% below max AIP",
    "discount_30pct": "30% below max AIP",
    "discount_40pct": "40% below max AIP",
}

PALIPERIDONE_SUPPLIER_NARRATIVE = (
    "Paliperidone has four listed suppliers in collected price schedules, but only "
    "Amdipharm and Janssen-Cilag have positive observed volume. Orifarm and Zentiva "
    "appear with explicit zero observed volume."
)


def readiness_category_matrix(matrix: pd.DataFrame) -> np.ndarray:
    numeric = matrix.replace(READINESS_LEVELS)
    if numeric.isna().any().any():
        raise ValueError("Readiness matrix contains unknown labels")
    return numeric.astype(int).values


def build_canonical_readiness_matrix(wide_df: pd.DataFrame) -> pd.DataFrame:
    """Return readiness categories indexed by molecule with fixed dimension column order."""
    canonical = wide_df.set_index("molecule").loc[list(MOLECULE_ORDER), list(READINESS_DIMENSION_ORDER)]
    for value in canonical.to_numpy().ravel():
        if value not in READINESS_LEVELS:
            raise ValueError(f"Invalid readiness category: {value!r}")
    return canonical


def readiness_rgba_for_label(label: str) -> tuple[float, float, float, float]:
    code = READINESS_LEVELS[label]
    return READINESS_CMAP(READINESS_NORM(code))


def readiness_cell_audit(canonical_df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_index, molecule in enumerate(MOLECULE_ORDER):
        for col_index, dimension in enumerate(READINESS_DIMENSION_ORDER):
            label = str(canonical_df.iloc[row_index, col_index])
            numeric = READINESS_LEVELS[label]
            rgba = readiness_rgba_for_label(label)
            rows.append({
                "molecule": molecule,
                "dimension": dimension,
                "rowIndex": row_index,
                "columnIndex": col_index,
                "category": label,
                "numericCategory": numeric,
                "rgba": rgba,
                "displayLabel": label,
                "textColor": readiness_text_color(label),
            })
    return rows


def readiness_text_color(label: str) -> str:
    return "white" if label == "Strong" else "black"


def classify_strength_volume(value: Any) -> str:
    if value == "" or value is None or (isinstance(value, float) and np.isnan(value)):
        return "unavailable"
    if float(value) == 0.0:
        return "zero"
    return "observed"


def strength_bar_value(value: Any) -> float | None:
    mode = classify_strength_volume(value)
    if mode == "observed":
        return float(value)
    if mode == "zero":
        return 0.0
    return None


def hhi_display_value(hhi: Any, concentration_coverage_pct: Any) -> str:
    coverage = float(concentration_coverage_pct) if concentration_coverage_pct not in ("", None) else 0.0
    if coverage <= 0:
        return "Unavailable"
    if hhi in ("", None) or (isinstance(hhi, float) and np.isnan(hhi)):
        return "Unavailable"
    return f"{float(hhi):.2f}"


def paliperidone_supplier_stats(supplier_df: pd.DataFrame) -> dict[str, Any]:
    mol = supplier_df[
        (supplier_df["molecule"] == "Paliperidone") & (supplier_df["recordType"] == "supplier_detail")
    ]
    summary = supplier_df[
        (supplier_df["molecule"] == "Paliperidone") & (supplier_df["recordType"] == "molecule_summary")
    ].iloc[0]
    listed = int(summary["supplierCount"]) if summary["supplierCount"] != "" else len(mol)
    positive = int((mol["observedVolume"].astype(float) > 0).sum())
    zero_listed = int((mol["observedVolume"].astype(float) == 0).sum())
    return {
        "listedSuppliers": listed,
        "positiveVolumeSuppliers": positive,
        "zeroVolumeListedSuppliers": zero_listed,
        "hhi": float(summary["HHI"]) if summary["HHI"] != "" else None,
        "excludedVolume": float(summary["volumeExcludedFromSupplierAnalysis"]),
        "concentrationCoveragePct": float(summary["concentrationCoveragePct"]),
    }


def molecule_volume_unavailable(supplier_summary_row: pd.Series) -> bool:
    coverage = supplier_summary_row.get("concentrationCoveragePct", 0)
    if coverage in ("", None):
        return True
    return float(coverage) <= 0
