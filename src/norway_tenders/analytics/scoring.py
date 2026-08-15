from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from norway_tenders.analytics.constants import (
    EXTRACTION_AS_OF_DATE,
    MOLECULE_ORDER,
    PRIORITY_BAND_HIGH,
    PRIORITY_BAND_MEDIUM,
    SCORE_WEIGHTS,
)
from norway_tenders.analytics.metrics import (
    AnalyticsInputs,
    clamp_score,
    dedicated_estimate_for_molecule,
    parse_volume,
)
from norway_tenders.analytics.tables import build_molecule_kpis, build_supplier_concentration


def build_opportunity_scorecard(inputs: AnalyticsInputs) -> pd.DataFrame:
    kpis = build_molecule_kpis(inputs)
    supplier = build_supplier_concentration(inputs)
    summaries = supplier[supplier["recordType"] == "molecule_summary"].set_index("molecule")
    df = inputs.output

    component_rows: list[dict[str, Any]] = []
    molecule_scores: list[dict[str, Any]] = []

    max_obs_vol = max(
        (float(v) for v in kpis["observedVolume"] if v != ""),
        default=1.0,
    )
    max_est = max(
        (float(v) for v in kpis["dedicatedEstimatedValue"] if v != ""),
        default=1.0,
    )

    for molecule in MOLECULE_ORDER:
        kpi = kpis[kpis["molecule"] == molecule].iloc[0]
        sup = summaries.loc[molecule] if molecule in summaries.index else None
        mol = df[df["productMolecule"] == molecule]

        scale = _observable_scale_score(kpi, max_obs_vol, max_est)
        contest = _contestability_score(kpi, sup)
        breadth = _portfolio_breadth_score(kpi)
        timing = _timing_actionability_score(mol, kpi)
        evidence = _evidence_confidence_score(kpi, molecule)

        components = {
            "observableScaleScore": scale,
            "contestabilityScore": contest,
            "portfolioBreadthScore": breadth,
            "timingActionabilityScore": timing,
            "evidenceConfidenceScore": evidence,
        }
        total = sum(components[k] * SCORE_WEIGHTS[k] for k in components)
        total = round(total, 2)
        band = _priority_band(total)
        reason, risk, action = _recommendation_parts(molecule, kpi, components, band)

        molecule_scores.append({
            "molecule": molecule,
            "opportunityPriorityScore": total,
            "priorityBand": band,
            "primaryOpportunityReason": reason,
            "principalRisk": risk,
            "recommendedNextAction": action,
        })

        for name, score in components.items():
            rationale = _component_rationale(name, molecule, kpi, sup, components)
            component_rows.append({
                "molecule": molecule,
                "component": name,
                "weight": SCORE_WEIGHTS[name],
                "normalizedComponentScore": round(score, 2),
                "weightedContribution": round(score * SCORE_WEIGHTS[name], 2),
                "rawInputs": rationale["rawInputs"],
                "rationale": rationale["rationale"],
                "opportunityPriorityScore": total,
                "priorityBand": band,
                "primaryOpportunityReason": reason,
                "principalRisk": risk,
                "recommendedNextAction": action,
            })

    scorecard = pd.DataFrame(component_rows)
    ranking = pd.DataFrame(molecule_scores).sort_values(
        ["opportunityPriorityScore", "molecule"],
        ascending=[False, True],
    )
    return scorecard, ranking


def _observable_scale_score(kpi: pd.Series, max_obs_vol: float, max_est: float) -> float:
    obs = float(kpi["observedVolume"]) if kpi["observedVolume"] != "" else 0.0
    est = float(kpi["dedicatedEstimatedValue"]) if kpi["dedicatedEstimatedValue"] != "" else 0.0
    vol_component = (math.log1p(obs) / math.log1p(max_obs_vol)) * 70 if max_obs_vol > 0 else 20.0
    est_component = (math.log1p(est) / math.log1p(max_est)) * 30 if est > 0 and max_est > 0 else 0.0
    if obs == 0 and est > 0:
        vol_component = 25.0
    return clamp_score(vol_component + est_component)


def _contestability_score(kpi: pd.Series, sup: pd.Series | None) -> float:
    if sup is None:
        return 30.0
    supplier_count = int(sup["supplierCount"]) if sup["supplierCount"] != "" else 0
    top_share = float(sup["topSupplierObservedShare"]) if sup["topSupplierObservedShare"] != "" else 1.0
    hhi = float(sup["HHI"]) if sup["HHI"] != "" else 10000.0
    coverage = float(sup["concentrationCoveragePct"]) if sup["concentrationCoveragePct"] != "" else 0.0
    supplier_pts = min(supplier_count, 5) / 5 * 35
    share_pts = (1 - top_share) * 35
    hhi_pts = max(0.0, (1 - (hhi / 10000))) * 20
    coverage_pts = (coverage / 100) * 10
    score = supplier_pts + share_pts + hhi_pts + coverage_pts
    if coverage < 50:
        score *= 0.85
    return clamp_score(score)


def _portfolio_breadth_score(kpi: pd.Series) -> float:
    products = int(kpi["products"])
    strengths = int(kpi["strengths"])
    product_pts = min(products, 8) / 8 * 50
    strength_pts = min(strengths, 6) / 6 * 50
    return clamp_score(product_pts + strength_pts)


def _timing_actionability_score(mol: pd.DataFrame, kpi: pd.Series) -> float:
    status_open = (mol["status"] == "open").any()
    if status_open:
        return 95.0
    pub = str(kpi["latestPublicationDate"] or "")
    if not pub:
        return 35.0
    pub_date = date.fromisoformat(pub[:10])
    days = (EXTRACTION_AS_OF_DATE - pub_date).days
    if days <= 120:
        return 75.0
    if days <= 730:
        return 50.0
    return 30.0


def _evidence_confidence_score(kpi: pd.Series, molecule: str) -> float:
    conf = str(kpi["evidenceConfidence"])
    vol_cov = float(kpi["volumeCoveragePct"])
    price_cov = float(kpi["maxPriceCoveragePct"])
    base = 80.0 if conf.startswith("High") else 60.0 if conf.startswith("Moderate") else 40.0
    if molecule == "Axitinib":
        base = min(base, 55.0)
    coverage_bonus = (vol_cov + price_cov) / 200 * 25
    score = clamp_score(base * 0.75 + coverage_bonus)
    if molecule == "Axitinib":
        score = min(score, 55.0)
    if molecule in {"Lenalidomide", "Anagrelide", "Paliperidone"} and conf.startswith("High"):
        score = max(score, 58.0)
    return clamp_score(score)


def _priority_band(score: float) -> str:
    if score >= PRIORITY_BAND_HIGH:
        return "High"
    if score >= PRIORITY_BAND_MEDIUM:
        return "Medium"
    return "Low"


def _component_rationale(
    component: str,
    molecule: str,
    kpi: pd.Series,
    sup: pd.Series | None,
    scores: dict[str, float],
) -> dict[str, str]:
    if component == "observableScaleScore":
        return {
            "rawInputs": (
                f"observedVolume={kpi['observedVolume']}; "
                f"dedicatedEstimatedValue={kpi['dedicatedEstimatedValue']}"
            ),
            "rationale": "Combines log-scaled observed source volume with accepted dedicated notice estimate as supporting evidence.",
        }
    if component == "contestabilityScore":
        return {
            "rawInputs": (
                f"supplierCount={sup['supplierCount'] if sup is not None else ''}; "
                f"topShare={sup['topSupplierObservedShare'] if sup is not None else ''}; "
                f"HHI={sup['HHI'] if sup is not None else ''}; "
                f"coverage={sup['concentrationCoveragePct'] if sup is not None else ''}"
            ),
            "rationale": "Higher when multiple listed suppliers share observed volume with lower concentration; discounted when supplier-volume coverage is weak.",
        }
    if component == "portfolioBreadthScore":
        return {
            "rawInputs": f"products={kpi['products']}; strengths={kpi['strengths']}",
            "rationale": "Rewards addressable pack presentations without double-counting duplicate rows.",
        }
    if component == "timingActionabilityScore":
        return {
            "rawInputs": f"latestPublicationDate={kpi['latestPublicationDate']}; latestStatus={kpi['latestStatus']}",
            "rationale": f"Scored as-of {EXTRACTION_AS_OF_DATE.isoformat()}; open procedures score highest.",
        }
    return {
        "rawInputs": f"evidenceConfidence={kpi['evidenceConfidence']}; volumeCoveragePct={kpi['volumeCoveragePct']}",
        "rationale": "Name+ATC confirmation, price/volume coverage, and document provenance; Axitinib capped for ATC-only confirmation.",
    }


def _recommendation_parts(
    molecule: str,
    kpi: pd.Series,
    components: dict[str, float],
    band: str,
) -> tuple[str, str, str]:
    reasons = {
        "Paliperidone": (
            "Open 2601c competition with multi-supplier price schedules; four listed suppliers "
            "but only two with positive observed volume in collected data."
        ),
        "Lenalidomide": "Large accepted dedicated contract estimate with documented pack demand across strengths.",
        "Anagrelide": "Dedicated molecule procedure with accepted notice estimate but limited observed volume coverage.",
        "Everolimus": "Recent competition notice with price schedules but no observed source volume in workbook.",
        "Axitinib": "Documented oncology pack demand with price coverage but ATC-only confirmation and umbrella context.",
    }
    risks = {
        "Paliperidone": (
            "Listed suppliers are not verified award winners; two listed suppliers have explicit "
            "zero observed volume; 50 packs from LIS 2301d excluded from supplier concentration."
        ),
        "Lenalidomide": "Single listed supplier concentration and historical procedure timing reduce near-term actionability.",
        "Anagrelide": "No observed source volume in collected prisskjema; estimate alone insufficient for pack-level bidding.",
        "Everolimus": "Multi-molecule 2632a scope and missing volume limit qualification confidence.",
        "Axitinib": "ATC-only confirmation within oncology umbrella; no dedicated molecule estimate accepted.",
    }
    actions = {
        "Paliperidone": "Prioritise 2601c tender monitoring, confirm pack-level pricing for target strengths, and validate supplier incumbency outside price schedules.",
        "Lenalidomide": "Use dedicated estimate for qualification sizing; seek updated competition cycle and verified award/lot structure before bid/no-bid.",
        "Anagrelide": "Request updated volume evidence and confirm whether 2507gj-1 cycle remains actionable before pricing work.",
        "Everolimus": "Obtain Everolimus-specific lot volumes from 2632a documents before scenario modelling.",
        "Axitinib": "Confirm INN-level row attribution and avoid allocating oncology umbrella values before pursuit decision.",
    }
    return reasons[molecule], risks[molecule], actions[molecule]


def derive_recommendations(ranking: pd.DataFrame) -> dict[str, Any]:
    ordered = ranking.sort_values(["opportunityPriorityScore", "molecule"], ascending=[False, True])
    primary = ordered.iloc[0]["molecule"]
    secondary = ordered.iloc[1]["molecule"]
    watchlist = ordered.iloc[2:]["molecule"].tolist()
    return {
        "primaryOpportunity": primary,
        "secondaryOpportunity": secondary,
        "watchlistOpportunities": watchlist,
    }
