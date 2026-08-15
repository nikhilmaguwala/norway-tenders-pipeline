from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch
from matplotlib.gridspec import GridSpec

from norway_tenders.analytics.chart_helpers import (
    READINESS_CMAP,
    READINESS_LEVEL_NAMES,
    READINESS_NORM,
    SCENARIO_LABELS,
    build_canonical_readiness_matrix,
    classify_strength_volume,
    hhi_display_value,
    molecule_volume_unavailable,
    readiness_category_matrix,
    readiness_text_color,
    strength_bar_value,
)
from norway_tenders.analytics.constants import (
    CHART_DPI,
    CHART_FIGSIZE,
    MOLECULE_ORDER,
    PALETTE,
    READINESS_DIMENSION_ORDER,
)
from norway_tenders.analytics.metrics import AnalyticsInputs
from norway_tenders.analytics.render_qa import inspect_figure
from norway_tenders.settings import CHARTS_DIR, TABLES_DIR

PRICING_UNAVAILABLE_CHART_LINES = {
    "Lenalidomide": "observed volume available, but no maxPrice",
    "Anagrelide": "partial price coverage, but no observed volume",
    "Everolimus": "maxPrice available, but no observed volume",
}

SUPPLIER_EVIDENCE_NOTES: dict[str, str] = {
    "Anagrelide": "volume unavailable; HHI unavailable",
    "Axitinib": (
        "one listed supplier represents 100% of covered observed volume; "
        "not proof of national monopoly or award"
    ),
    "Everolimus": "volume unavailable; HHI unavailable",
    "Lenalidomide": (
        "one listed supplier represents 100% of covered observed volume; "
        "not proof of national monopoly or award"
    ),
    "Paliperidone": (
        "4 listed suppliers; 2 positive-volume suppliers; HHI 0.51; 99.4% coverage; "
        "50 packs excluded because supplier is blank"
    ),
}


def _save(
    fig: plt.Figure,
    stem: str,
    *,
    missing_data_treatment: str,
    overlap_axes_ids: set[int] | None = None,
) -> list[Path]:
    inspect_figure(
        fig,
        stem,
        missing_data_treatment=missing_data_treatment,
        overlap_axes_ids=overlap_axes_ids,
        dpi=CHART_DPI,
    )
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    png = CHARTS_DIR / f"{stem}.png"
    svg = CHARTS_DIR / f"{stem}.svg"
    fig.set_size_inches(CHART_FIGSIZE[0], CHART_FIGSIZE[1])
    fig.savefig(png, dpi=CHART_DPI, facecolor="white")
    fig.savefig(svg, facecolor="white")
    plt.close(fig)
    return [png, svg]


def _render_wrapped_panel(ax: plt.Axes, lines: list[str], *, fontsize: float = 9.0) -> None:
    ax.axis("off")
    content = "\n".join(lines)
    ax.text(
        0.0,
        1.0,
        content,
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=fontsize,
        color="#222222",
        linespacing=1.35,
    )


def _label_offsets(molecule: str) -> tuple[int, int]:
    offsets = {
        "Paliperidone": (8, -10),
        "Lenalidomide": (-55, 8),
        "Axitinib": (8, 8),
        "Anagrelide": (8, -12),
        "Everolimus": (-50, -8),
    }
    return offsets.get(molecule, (6, 6))


def chart_opportunity_priority(
    ranking: pd.DataFrame,
    scorecard: pd.DataFrame,
    kpis: pd.DataFrame,
) -> list[Path]:
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE)
    x_vals, y_vals, sizes, colors, labels = [], [], [], [], []
    for molecule in MOLECULE_ORDER:
        row = ranking[ranking["molecule"] == molecule].iloc[0]
        contest = float(
            scorecard[
                (scorecard["molecule"] == molecule)
                & (scorecard["component"] == "contestabilityScore")
            ]["normalizedComponentScore"].iloc[0]
        )
        scale = float(
            scorecard[
                (scorecard["molecule"] == molecule)
                & (scorecard["component"] == "observableScaleScore")
            ]["normalizedComponentScore"].iloc[0]
        )
        kpi_row = kpis[kpis["molecule"] == molecule].iloc[0]
        kpi_vol = float(kpi_row["observedVolume"]) if kpi_row["observedVolume"] != "" else 0.0
        bubble = max(kpi_vol, 50) ** 0.5 * 30
        x_vals.append(contest)
        y_vals.append(scale)
        sizes.append(bubble)
        colors.append(PALETTE.get(row["priorityBand"], "#888888"))
        labels.append(molecule)
    ax.scatter(x_vals, y_vals, s=sizes, c=colors, alpha=0.75, edgecolors="white", linewidths=1.2, zorder=2)
    for x, y, label in zip(x_vals, y_vals, labels, strict=True):
        dx, dy = _label_offsets(label)
        ax.annotate(
            label,
            (x, y),
            textcoords="offset points",
            xytext=(dx, dy),
            fontsize=11,
            fontweight="bold",
            zorder=3,
        )
    ax.set_xlabel("Contestability score (heuristic)")
    ax.set_ylabel("Observable scale score (heuristic)")
    ax.set_title(
        "Opportunity prioritisation matrix\nPrioritisation heuristic — not win probability",
        fontsize=14,
        fontweight="bold",
    )
    ax.grid(True, alpha=0.25)
    ax.margins(0.15)
    legend_handles = [
        Patch(facecolor=PALETTE["High"], label="High priority"),
        Patch(facecolor=PALETTE["Medium"], label="Medium priority"),
        Patch(facecolor=PALETTE["Low"], label="Low priority"),
    ]
    ax.legend(handles=legend_handles, loc="lower right")
    fig.text(
        0.02,
        0.02,
        "Bubble size = observed source pack volume; minimum bubble size indicates unavailable/low "
        "observed volume and must not be interpreted as zero market demand. "
        "Anagrelide observable-scale score is supported by its accepted dedicated NOK 10M notice "
        "estimate despite missing pack volume. Coverage limited to collected procedures.",
        fontsize=9,
        color="#444444",
        wrap=True,
    )
    fig.subplots_adjust(bottom=0.18, left=0.08, right=0.96, top=0.88)
    return _save(
        fig,
        "01_opportunity_priority",
        missing_data_treatment="minimum bubble size for unavailable/low observed volume",
    )


def chart_strength_demand(strength_df: pd.DataFrame) -> list[Path]:
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE)
    plot_rows: list[tuple[str, float | None, str, str]] = []
    for molecule in MOLECULE_ORDER:
        mol = strength_df[strength_df["molecule"] == molecule].copy()
        mol = mol.sort_values("observedVolume", ascending=True, na_position="first")
        for _, row in mol.iterrows():
            mode = classify_strength_volume(row["observedVolume"])
            bar_val = strength_bar_value(row["observedVolume"])
            label = f"{molecule} | {row['strength']}"
            plot_rows.append((label, bar_val, molecule, mode))

    labels = [r[0] for r in plot_rows]
    modes = [r[3] for r in plot_rows]
    y_pos = np.arange(len(labels))
    observed_vals = [r[1] for r in plot_rows if r[3] == "observed"]
    xmax = max(observed_vals) if observed_vals else 1.0
    placeholder = xmax * 0.02
    bars = []
    for i, (bar_val, mode, mol) in enumerate(
        zip([r[1] for r in plot_rows], modes, [r[2] for r in plot_rows], strict=True)
    ):
        if mode == "unavailable":
            bar = ax.barh(
                i,
                placeholder,
                color=PALETTE["unavailable"],
                alpha=0.85,
                hatch="///",
                edgecolor="#666666",
                linewidth=0.8,
            )
            ax.text(placeholder * 1.2, i, "Volume unavailable", va="center", fontsize=8, color="#444444")
        elif mode == "zero":
            bar = ax.barh(i, 0.0, color=PALETTE.get(mol, "#888888"), alpha=0.85, edgecolor="none")
            ax.text(0.02, i, "0 observed packs", va="center", fontsize=8, color="#444444")
        else:
            bar = ax.barh(
                i,
                bar_val,
                color=PALETTE.get(mol, "#888888"),
                alpha=0.85,
                edgecolor="none",
            )
        bars.append(bar[0])

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Observed source pack volume")
    ax.set_title(
        "Strength demand profile by molecule (grouped bars)\nObserved source pack volume — not market size",
        fontsize=14,
        fontweight="bold",
    )

    for molecule in MOLECULE_ORDER:
        mol = strength_df[strength_df["molecule"] == molecule]
        mol = mol[mol["observedVolume"] != ""]
        if mol.empty:
            continue
        cutoff = mol[mol["cumulativeVolumeShare"] <= 0.8]
        if cutoff.empty:
            continue
        last = cutoff.iloc[-1]["strength"]
        for i, lbl in enumerate(labels):
            if lbl.startswith(f"{molecule} | {last}") and modes[i] == "observed":
                bars[i].set_edgecolor("black")
                bars[i].set_linewidth(2)

    legend_handles = [
        Patch(facecolor=PALETTE["Lenalidomide"], label="Observed volume"),
        Patch(facecolor="white", edgecolor="black", linewidth=2, label="Within cumulative ~80%"),
        Patch(facecolor=PALETTE["unavailable"], hatch="///", edgecolor="#666666", label="Volume unavailable"),
    ]
    ax.legend(handles=legend_handles, loc="lower right", fontsize=9)
    fig.subplots_adjust(left=0.28, right=0.96, bottom=0.08, top=0.88)
    return _save(
        fig,
        "02_strength_demand_heatmap",
        missing_data_treatment="grey hatched placeholder with Volume unavailable label",
    )


def chart_supplier_concentration(supplier_df: pd.DataFrame) -> list[Path]:
    fig = plt.figure(figsize=CHART_FIGSIZE)
    gs = GridSpec(2, 2, figure=fig, height_ratios=[3.2, 1.35], width_ratios=[2.6, 1.1], hspace=0.42, wspace=0.28)
    ax = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax_notes = fig.add_subplot(gs[1, :])
    summaries = supplier_df[supplier_df["recordType"] == "molecule_summary"].set_index("molecule")
    x = np.arange(len(MOLECULE_ORDER))
    width = 0.65
    supplier_colors: dict[str, str] = {}
    color_cycle = plt.cm.Set3(np.linspace(0, 1, 12))
    all_suppliers = supplier_df[supplier_df["recordType"] == "supplier_detail"]["supplier"].unique().tolist()
    for i, sup in enumerate(all_suppliers):
        supplier_colors[sup] = color_cycle[i % len(color_cycle)]

    for i, molecule in enumerate(MOLECULE_ORDER):
        summary = summaries.loc[molecule]
        if molecule_volume_unavailable(summary):
            ax.bar(i, 1.0, width, color=PALETTE["unavailable"], hatch="///", edgecolor="#666666")
            ax.text(i, 0.5, "Volume\nunavailable", ha="center", va="center", fontsize=9, color="#333333")
            continue

        bottom = 0.0
        details = supplier_df[
            (supplier_df["recordType"] == "supplier_detail") & (supplier_df["molecule"] == molecule)
        ]
        for _, row in details.iterrows():
            share = float(row["observedVolumeShare"])
            vol = float(row["observedVolume"])
            if vol <= 0:
                continue
            ax.bar(i, share, width, bottom=bottom, color=supplier_colors[row["supplier"]])
            bottom += share
        excl = float(summary["volumeExcludedFromSupplierAnalysis"] or 0)
        total = float(summary["observedVolume"] or 0) + excl
        if excl > 0 and total > 0:
            excl_share = excl / total
            ax.bar(i, excl_share, width, bottom=bottom, color=PALETTE["excluded"])

    handles, labels_seen = [], []
    for sup in all_suppliers:
        handles.append(Patch(facecolor=supplier_colors[sup], label=sup))
        labels_seen.append(sup)
    zero_suppliers = supplier_df[
        (supplier_df["recordType"] == "supplier_detail")
        & (pd.to_numeric(supplier_df["observedVolume"], errors="coerce") == 0)
    ]["supplier"].unique()
    for sup in zero_suppliers:
        if sup not in labels_seen:
            handles.append(
                Patch(
                    facecolor="white",
                    edgecolor=supplier_colors.get(sup, "#999999"),
                    hatch="..",
                    label=f"{sup} (0 vol.)",
                )
            )
    handles.append(Patch(facecolor=PALETTE["excluded"], label="Excluded / unassigned volume"))
    handles.append(Patch(facecolor=PALETTE["unavailable"], hatch="///", edgecolor="#666666", label="Volume unavailable"))

    ax.set_xticks(x)
    ax.set_xticklabels(MOLECULE_ORDER, rotation=20, ha="right")
    ax.set_ylabel("Share of observed source volume")
    ax.set_ylim(0, 1.02)
    ax.set_title(
        "Observed-source supplier concentration — not market share",
        fontsize=14,
        fontweight="bold",
    )
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=3, fontsize=8)

    hhi_vals = []
    for molecule in MOLECULE_ORDER:
        summary = summaries.loc[molecule]
        display = hhi_display_value(summary["HHI"], summary["concentrationCoveragePct"])
        hhi_vals.append(np.nan if display == "Unavailable" else float(display))
    ax2.barh(MOLECULE_ORDER, hhi_vals, color="#72B7B2", alpha=0.8)
    ax2.set_xlim(0, 1.0)
    ax2.set_xlabel("HHI (observed volume)")
    ax2.set_title("Concentration metrics", fontsize=11)
    for i, molecule in enumerate(MOLECULE_ORDER):
        summary = summaries.loc[molecule]
        display = hhi_display_value(summary["HHI"], summary["concentrationCoveragePct"])
        if display == "Unavailable":
            ax2.text(0.03, i, "HHI unavailable", va="center", fontsize=8, color="#444444")
        else:
            hhi_val = float(display)
            top = summary["topSupplierObservedShare"]
            top_pct = f"{float(top) * 100:.0f}%" if top not in ("", None) else ""
            label = f"HHI {display}; top {top_pct}"
            if hhi_val >= 0.85:
                ax2.text(hhi_val * 0.55, i, label, ha="center", va="center", fontsize=7.5, color="white")
            else:
                ax2.text(hhi_val + 0.03, i, label, ha="left", va="center", fontsize=7.5)

    note_lines = ["Evidence notes", ""]
    for molecule in MOLECULE_ORDER:
        wrapped = textwrap.fill(
            f"{molecule}: {SUPPLIER_EVIDENCE_NOTES[molecule]}",
            width=118,
        )
        note_lines.extend(wrapped.splitlines())
        note_lines.append("")
    _render_wrapped_panel(ax_notes, note_lines, fontsize=8.5)

    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.06, top=0.92)
    return _save(
        fig,
        "03_supplier_concentration",
        missing_data_treatment="grey hatched bars for volume unavailable molecules",
    )


def chart_pricing_scenarios(pricing_df: pd.DataFrame) -> list[Path]:
    fig = plt.figure(figsize=CHART_FIGSIZE)
    gs = GridSpec(2, 2, figure=fig, height_ratios=[4, 0.55], width_ratios=[2.1, 1.15], hspace=0.28, wspace=0.22)
    ax = fig.add_subplot(gs[0, 0])
    ax_info = fig.add_subplot(gs[0, 1])
    ax_footer = fig.add_subplot(gs[1, :])
    ax_footer.axis("off")

    molecules_with_data = []
    for molecule in MOLECULE_ORDER:
        ref = pricing_df[
            (pricing_df["recordType"] == "molecule_summary")
            & (pricing_df["molecule"] == molecule)
            & (pricing_df["scenarioName"] == "reference_at_max_aip")
        ]
        if not ref.empty and ref.iloc[0]["scenarioGrossValue"] != "":
            molecules_with_data.append(molecule)

    scenario_order = [
        "reference_at_max_aip",
        "discount_5pct",
        "discount_10pct",
        "discount_20pct",
        "discount_30pct",
        "discount_40pct",
    ]
    if molecules_with_data:
        x = np.arange(len(molecules_with_data))
        width = 0.13
        for i, scenario in enumerate(scenario_order):
            vals = []
            for molecule in molecules_with_data:
                row = pricing_df[
                    (pricing_df["recordType"] == "molecule_summary")
                    & (pricing_df["molecule"] == molecule)
                    & (pricing_df["scenarioName"] == scenario)
                ]
                val = row.iloc[0]["scenarioGrossValue"] if not row.empty else ""
                vals.append(float(val) / 1_000_000 if val != "" else np.nan)
            offset = (i - len(scenario_order) / 2) * width
            ax.bar(x + offset, vals, width, label=SCENARIO_LABELS[scenario])
        ax.set_xticks(x)
        ax.set_xticklabels(molecules_with_data, rotation=0)
        ax.set_ylabel("Reference gross value (NOK millions)")
        ax.legend(fontsize=8, loc="upper left")

    unavailable_lines = ["Unavailable in collected data:", ""]
    for molecule in MOLECULE_ORDER:
        if molecule not in molecules_with_data:
            line = f"{molecule}: {PRICING_UNAVAILABLE_CHART_LINES[molecule]}"
            unavailable_lines.extend(textwrap.wrap(line, width=42))
            unavailable_lines.append("")
    _render_wrapped_panel(ax_info, unavailable_lines, fontsize=9.5)

    ax.set_title("Pricing reference scenarios by molecule", fontsize=14, fontweight="bold")
    footer = textwrap.fill(
        "Reference gross value = maxPrice × observed source volume. Maximum AIP is a reference ceiling, "
        "not achieved tender price. DMP 2026-08-03 values are not historical tender-time prices. "
        "Scenarios are not bid recommendations.",
        width=150,
    )
    _render_wrapped_panel(ax_footer, [footer], fontsize=9)
    fig.subplots_adjust(left=0.07, right=0.98, bottom=0.04, top=0.90)
    return _save(
        fig,
        "04_pricing_scenarios",
        missing_data_treatment="side panel lists unavailable molecules with full reasons",
    )


def chart_tender_readiness(wide_df: pd.DataFrame) -> list[Path]:
    canonical = build_canonical_readiness_matrix(wide_df)
    numeric = readiness_category_matrix(canonical)

    fig = plt.figure(figsize=CHART_FIGSIZE)
    gs = GridSpec(1, 2, figure=fig, width_ratios=[2.15, 1.25], wspace=0.12)
    ax = fig.add_subplot(gs[0, 0])
    ax_actions = fig.add_subplot(gs[0, 1])

    im = ax.imshow(numeric, aspect="auto", cmap=READINESS_CMAP, norm=READINESS_NORM)
    ax.set_xticks(np.arange(len(READINESS_DIMENSION_ORDER)))
    ax.set_yticks(np.arange(len(MOLECULE_ORDER)))
    ax.set_xticklabels([d.replace(" ", "\n") for d in READINESS_DIMENSION_ORDER], fontsize=9)
    ax.set_yticklabels(MOLECULE_ORDER, fontsize=10)
    for row_index in range(len(MOLECULE_ORDER)):
        for col_index in range(len(READINESS_DIMENSION_ORDER)):
            label = str(canonical.iloc[row_index, col_index])
            ax.text(
                col_index,
                row_index,
                label,
                ha="center",
                va="center",
                color=readiness_text_color(label),
                fontsize=10,
                fontweight="bold",
            )
    ax.set_title("Tender readiness and evidence coverage", fontsize=14, fontweight="bold")
    cbar = fig.colorbar(im, ax=ax, ticks=[0, 1, 2], shrink=0.75, pad=0.02)
    cbar.ax.set_yticklabels(list(READINESS_LEVEL_NAMES))

    action_lines = ["Next actions", ""]
    for molecule in MOLECULE_ORDER:
        action = str(wide_df.loc[wide_df["molecule"] == molecule, "nextAction"].iloc[0])
        wrapped = textwrap.fill(f"{molecule}: {action}", width=44)
        action_lines.extend(wrapped.splitlines())
        action_lines.append("")
    _render_wrapped_panel(ax_actions, action_lines, fontsize=8.5)

    fig.subplots_adjust(left=0.15, right=0.98, bottom=0.06, top=0.90)
    return _save(
        fig,
        "05_tender_readiness",
        missing_data_treatment="canonical matrix drives both imshow colours and cell labels",
    )


def write_readiness_matrix_csv(wide_df: pd.DataFrame, path: Path | None = None) -> Path:
    out = path or (TABLES_DIR / "readiness_matrix.csv")
    canonical = build_canonical_readiness_matrix(wide_df)
    canonical.reset_index().to_csv(out, index=False, encoding="utf-8")
    return out


def build_readiness_wide(inputs: AnalyticsInputs, kpis: pd.DataFrame, ranking: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for molecule in MOLECULE_ORDER:
        kpi = kpis[kpis["molecule"] == molecule].iloc[0]
        rank = ranking[ranking["molecule"] == molecule].iloc[0]
        mol = inputs.output[inputs.output["productMolecule"] == molecule]
        vol_cov = float(kpi["volumeCoveragePct"])
        price_cov = float(kpi["maxPriceCoveragePct"])
        supplier_cov = 100 * (mol["supplier"].notna() & (mol["supplier"] != "")).mean()
        row = {
            "molecule": molecule,
            "nextAction": rank["recommendedNextAction"],
            "molecule confirmation": (
                "Strong" if str(kpi["evidenceConfidence"]).startswith("High") else "Partial"
            ),
            "current/open timing": (
                "Strong" if (mol["status"] == "open").any() else "Partial" if vol_cov else "Missing"
            ),
            "volume coverage": (
                "Strong" if vol_cov >= 70 else "Partial" if vol_cov > 0 else "Missing"
            ),
            "price coverage": (
                "Strong" if price_cov >= 70 else "Partial" if price_cov > 0 else "Missing"
            ),
            "supplier coverage": (
                "Strong" if supplier_cov >= 70 else "Partial" if supplier_cov > 0 else "Missing"
            ),
            "dedicated estimate availability": (
                "Strong" if kpi["dedicatedEstimatedValue"] != "" else "Missing"
            ),
            "award evidence availability": "Missing",
        }
        rows.append(row)
    return pd.DataFrame(rows)


def build_readiness_table(inputs: AnalyticsInputs, kpis: pd.DataFrame, ranking: pd.DataFrame) -> pd.DataFrame:
    wide = build_readiness_wide(inputs, kpis, ranking)
    long_rows = []
    for _, row in wide.iterrows():
        for dim in READINESS_DIMENSION_ORDER:
            long_rows.append({
                "molecule": row["molecule"],
                "dimension": dim,
                "readiness": row[dim],
                "nextAction": row["nextAction"],
            })
    return pd.DataFrame(long_rows)
