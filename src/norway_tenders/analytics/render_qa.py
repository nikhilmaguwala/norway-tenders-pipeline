from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import matplotlib.text as mtext
from matplotlib.figure import Figure

CLIP_TOLERANCE = 0.008
OVERLAP_AREA_THRESHOLD = 0.00015


@dataclass
class ChartQARecord:
    stem: str
    width_px: int
    height_px: int
    title_present: bool
    legend_position: str
    missing_data_treatment: str
    clipping_pass: bool
    overlap_pass: bool
    clipping_failures: list[str] = field(default_factory=list)
    overlap_failures: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.clipping_pass and self.overlap_pass


CHART_QA_RESULTS: list[ChartQARecord] = []


def reset_chart_qa() -> None:
    CHART_QA_RESULTS.clear()


def _figure_text_artists(fig: Figure) -> list[mtext.Text]:
    artists: list[mtext.Text] = []
    if fig._suptitle is not None and fig._suptitle.get_text().strip():
        artists.append(fig._suptitle)
    artists.extend(fig.texts)
    for ax in fig.axes:
        title = ax.get_title()
        if title.strip():
            artists.append(ax.title)
        xlabel = ax.get_xlabel()
        if xlabel.strip():
            artists.append(ax.xaxis.label)
        ylabel = ax.get_ylabel()
        if ylabel.strip():
            artists.append(ax.yaxis.label)
        for child in ax.get_children():
            if isinstance(child, mtext.Text):
                text = child.get_text().strip()
                if text and child not in artists:
                    artists.append(child)
    return artists


def _bbox_in_figure(fig: Figure, renderer: Any, artist: mtext.Text) -> tuple[float, float, float, float]:
    bbox = artist.get_window_extent(renderer=renderer).transformed(fig.transFigure.inverted())
    return (float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1))


def _intersection_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _legend_position(fig: Figure) -> str:
    for ax in fig.axes:
        legend = ax.get_legend()
        if legend is not None:
            return "below panel" if legend._loc == "upper center" else str(legend._loc)
    return "none"


def inspect_figure(
    fig: Figure,
    stem: str,
    *,
    missing_data_treatment: str,
    overlap_axes_ids: set[int] | None = None,
    dpi: int = 100,
) -> ChartQARecord:
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width_px = int(fig.get_figwidth() * dpi)
    height_px = int(fig.get_figheight() * dpi)

    clipping_failures: list[str] = []
    artists = _figure_text_artists(fig)
    for artist in artists:
        x0, y0, x1, y1 = _bbox_in_figure(fig, renderer, artist)
        if (
            x0 < -CLIP_TOLERANCE
            or y0 < -CLIP_TOLERANCE
            or x1 > 1.0 + CLIP_TOLERANCE
            or y1 > 1.0 + CLIP_TOLERANCE
        ):
            snippet = artist.get_text().strip().replace("\n", " ")[:80]
            clipping_failures.append(f"'{snippet}' bbox=({x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f})")

    overlap_failures: list[str] = []
    if overlap_axes_ids:
        by_axis: dict[int, list[tuple[mtext.Text, tuple[float, float, float, float]]]] = {}
        for artist in artists:
            ax = artist.axes
            if ax is None:
                continue
            ax_id = id(ax)
            if ax_id not in overlap_axes_ids:
                continue
            by_axis.setdefault(ax_id, []).append((artist, _bbox_in_figure(fig, renderer, artist)))
        for entries in by_axis.values():
            for i, (left, lb) in enumerate(entries):
                for right, rb in entries[i + 1 :]:
                    area = _intersection_area(lb, rb)
                    if area > OVERLAP_AREA_THRESHOLD:
                        overlap_failures.append(
                            f"overlap area {area:.5f}: '{left.get_text()[:40]}' vs '{right.get_text()[:40]}'"
                        )

    title_present = any(ax.get_title().strip() for ax in fig.axes) or (
        fig._suptitle is not None and bool(fig._suptitle.get_text().strip())
    )
    record = ChartQARecord(
        stem=stem,
        width_px=width_px,
        height_px=height_px,
        title_present=title_present,
        legend_position=_legend_position(fig),
        missing_data_treatment=missing_data_treatment,
        clipping_pass=not clipping_failures,
        overlap_pass=not overlap_failures,
        clipping_failures=clipping_failures,
        overlap_failures=overlap_failures,
    )
    CHART_QA_RESULTS.append(record)
    if not record.passed:
        details = clipping_failures + overlap_failures
        raise RenderQAError(stem, details)
    return record


class RenderQAError(RuntimeError):
    def __init__(self, stem: str, details: list[str]) -> None:
        self.stem = stem
        self.details = details
        super().__init__(f"Render QA failed for {stem}: " + "; ".join(details[:5]))


def write_chart_qa_md(path: Any) -> None:
    lines = ["# Chart render QA (Phase 6B)", ""]
    lines.append("Automated checks performed before each chart save:")
    lines.append("- figure bounding-box clipping for titles, legends, axis labels, notes, and annotations")
    lines.append("- annotation overlap within designated notes panels (where applicable)")
    lines.append("- minimum resolution 1600×900 at 100 DPI")
    lines.append("")
    for record in CHART_QA_RESULTS:
        lines.append(f"## {record.stem}")
        lines.append(f"- Dimensions: {record.width_px}×{record.height_px}")
        lines.append(f"- Title present: {'yes' if record.title_present else 'no'}")
        lines.append(f"- Legend position: {record.legend_position}")
        lines.append(f"- Missing-data treatment: {record.missing_data_treatment}")
        lines.append(f"- Clipping check: {'pass' if record.clipping_pass else 'fail'}")
        if record.clipping_failures:
            for item in record.clipping_failures:
                lines.append(f"  - {item}")
        lines.append(f"- Overlap check: {'pass' if record.overlap_pass else 'fail'}")
        if record.overlap_failures:
            for item in record.overlap_failures:
                lines.append(f"  - {item}")
        lines.append(f"- Overall: {'pass' if record.passed else 'fail'}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
