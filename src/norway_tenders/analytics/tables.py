from __future__ import annotations

from typing import Any

import pandas as pd

from norway_tenders.analytics.constants import DISCOUNT_PCTS, MOLECULE_ORDER
from norway_tenders.analytics.metrics import (
    AnalyticsInputs,
    calculate_hhi,
    dedicated_estimate_for_molecule,
    format_nok,
    normalize_strength,
    parse_optional_float,
    parse_volume,
)


def _volume_series(df: pd.DataFrame) -> pd.Series:
    return df["packsSoldLast12m"].apply(parse_volume)


def build_molecule_kpis(inputs: AnalyticsInputs) -> pd.DataFrame:
    df = inputs.output
    rows: list[dict[str, Any]] = []
    for molecule in MOLECULE_ORDER:
        mol = df[df["productMolecule"] == molecule]
        volumes = _volume_series(mol)
        vol_present = volumes.dropna()
        observed = float(vol_present.sum()) if not vol_present.empty else None
        vol_cov = round(100 * len(vol_present) / max(len(mol), 1), 1)
        zero_rows = int((volumes == 0).sum())
        prices = mol["maxPrice"].dropna()
        procedures = mol[["noticeId", "tenderRef"]].drop_duplicates()
        latest_pub = ""
        latest_ref = ""
        latest_status = ""
        if not mol.empty:
            pub_dates = mol[mol["publicationDate"].notna() & (mol["publicationDate"] != "")]
            if not pub_dates.empty:
                latest_row = pub_dates.sort_values("publicationDate").iloc[-1]
                latest_pub = str(latest_row["publicationDate"])
                latest_ref = str(latest_row["tenderRef"])
                latest_status = str(latest_row.get("status") or "")
        dedicated_est = dedicated_estimate_for_molecule(
            df, molecule, inputs.accepted_estimate_notices,
        )
        confidence, limitation = _evidence_confidence(mol, molecule)
        rows.append({
            "molecule": molecule,
            "packRows": len(mol),
            "procedures": len(procedures),
            "observedVolume": observed if observed is not None else "",
            "volumeCoveragePct": vol_cov,
            "explicitZeroVolumeRows": zero_rows,
            "products": mol["productName"].nunique(),
            "strengths": mol["strength"].nunique(),
            "listedSuppliers": mol[mol["supplier"].notna() & (mol["supplier"] != "")]["supplier"].nunique(),
            "maxPriceCoveragePct": round(100 * prices.notna().sum() / max(len(mol), 1), 1),
            "minimumMaxPrice": float(prices.min()) if not prices.empty else "",
            "medianMaxPrice": float(prices.median()) if not prices.empty else "",
            "maximumMaxPrice": float(prices.max()) if not prices.empty else "",
            "dedicatedEstimatedValue": dedicated_est if dedicated_est is not None else "",
            "latestPublicationDate": latest_pub,
            "latestTenderRef": latest_ref,
            "latestStatus": latest_status,
            "evidenceConfidence": confidence,
            "importantLimitation": limitation,
        })
    return pd.DataFrame(rows)


def _evidence_confidence(mol: pd.DataFrame, molecule: str) -> tuple[str, str]:
    name_confirmed = (mol["moleculeDetected"] == "True").any() or (
        mol["detectionMethod"] == "name_in_document"
    ).any()
    atc_only = (mol["detectionMethod"] == "atc_in_document").all()
    vol_cov = mol["packsSoldLast12m"].apply(parse_volume).notna().mean()
    price_cov = mol["maxPrice"].notna().mean()
    if molecule == "Axitinib" or atc_only:
        confidence = "Moderate (ATC/brand only)"
        limitation = (
            "Axitinib rows are confirmed by ATC L01EK01 and Inlyta brand without explicit INN "
            "text in pack rows; oncology umbrella context limits estimate allocation."
        )
    elif name_confirmed and price_cov >= 0.5 and vol_cov >= 0.5:
        confidence = "High (name + ATC + document packs)"
        limitation = "Limited to collected procedures; award outcomes not verified."
    elif name_confirmed:
        confidence = "Moderate (name + ATC; partial price/volume coverage)"
        limitation = "Volume and/or price gaps remain in source workbooks."
    else:
        confidence = "Low"
        limitation = "Weak molecule confirmation or sparse workbook evidence."
    if molecule == "Paliperidone":
        limitation += " Two procedure cycles (LIS 2301d VEAT and open 2601c) must not be merged."
    if molecule == "Everolimus":
        limitation += " Multi-molecule 2632a procedure; umbrella estimate excluded."
    return confidence, limitation


def build_supplier_concentration(inputs: AnalyticsInputs) -> pd.DataFrame:
    df = inputs.output
    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for molecule in MOLECULE_ORDER:
        mol = df[df["productMolecule"] == molecule]
        volumes = _volume_series(mol)
        mol = mol.assign(_volume=volumes)
        analyzable = mol[
            mol["supplier"].notna() & (mol["supplier"] != "") & mol["_volume"].notna()
        ].copy()
        excluded_volume = float(
            mol.loc[
                (mol["supplier"].isna() | (mol["supplier"] == "")) & mol["_volume"].notna(),
                "_volume",
            ].sum()
        )
        total_analyzable = float(analyzable["_volume"].sum()) if not analyzable.empty else 0.0
        supplier_groups = (
            analyzable.groupby("supplier", dropna=False)
            .agg(observedVolume=("_volume", "sum"), packRows=("itemNumber", "count"), strengths=("strength", "nunique"))
            .reset_index()
            .sort_values("observedVolume", ascending=False)
        )
        shares: list[float] = []
        for rank, row in enumerate(supplier_groups.itertuples(index=False), start=1):
            share = (row.observedVolume / total_analyzable) if total_analyzable > 0 else 0.0
            shares.append(share)
            detail_rows.append({
                "recordType": "supplier_detail",
                "molecule": molecule,
                "supplier": row.supplier,
                "observedVolume": row.observedVolume,
                "observedVolumeShare": round(share, 4),
                "packRows": int(row.packRows),
                "strengths": int(row.strengths),
                "rankWithinMolecule": rank,
                "supplierCount": "",
                "topSupplierObservedShare": "",
                "HHI": "",
                "volumeExcludedFromSupplierAnalysis": "",
                "concentrationCoveragePct": "",
            })
        hhi = calculate_hhi(shares)
        coverage_pct = round(
            100 * total_analyzable / max(float(volumes.dropna().sum()), 1), 1,
        ) if volumes.notna().any() else 0.0
        summary_rows.append({
            "recordType": "molecule_summary",
            "molecule": molecule,
            "supplier": "",
            "observedVolume": total_analyzable,
            "observedVolumeShare": "",
            "packRows": len(mol),
            "strengths": mol["strength"].nunique(),
            "rankWithinMolecule": "",
            "supplierCount": int(supplier_groups.shape[0]),
            "topSupplierObservedShare": round(shares[0], 4) if shares else "",
            "HHI": hhi if hhi is not None else "",
            "volumeExcludedFromSupplierAnalysis": excluded_volume,
            "concentrationCoveragePct": coverage_pct,
        })

    return pd.DataFrame(detail_rows + summary_rows)


def build_strength_demand(inputs: AnalyticsInputs) -> pd.DataFrame:
    df = inputs.output
    rows: list[dict[str, Any]] = []
    for molecule in MOLECULE_ORDER:
        mol = df[df["productMolecule"] == molecule].copy()
        mol["normalizedStrength"] = mol["strength"].apply(normalize_strength)
        mol["_volume"] = _volume_series(mol)
        total_vol = float(mol["_volume"].dropna().sum())
        strength_groups = (
            mol.groupby("normalizedStrength", dropna=False)
            .agg(
                packRows=("itemNumber", "count"),
                observedVolume=("_volume", lambda s: float(s.dropna().sum()) if s.notna().any() else None),
                suppliers=("supplier", lambda s: s[s.notna() & (s != "")].nunique()),
                maxPriceCoveragePct=("maxPrice", lambda s: round(100 * s.notna().sum() / len(s), 1)),
            )
            .reset_index()
            .sort_values("observedVolume", ascending=False, na_position="last")
        )
        cumulative = 0.0
        for _, row in strength_groups.iterrows():
            vol = row["observedVolume"]
            share = (vol / total_vol) if (vol is not None and total_vol > 0) else ""
            if vol is not None and total_vol > 0:
                cumulative += float(vol) / total_vol
            subset = mol[mol["normalizedStrength"] == row["normalizedStrength"]]
            priced = subset[subset["maxPrice"].notna() & subset["_volume"].notna()]
            weighted = ""
            if not priced.empty and float(priced["_volume"].sum()) > 0:
                weighted = round(
                    float((priced["maxPrice"] * priced["_volume"]).sum() / priced["_volume"].sum()),
                    2,
                )
            rows.append({
                "molecule": molecule,
                "strength": row["normalizedStrength"],
                "packRows": int(row["packRows"]),
                "observedVolume": vol if vol is not None else "",
                "volumeShareWithinMolecule": round(share, 4) if share != "" else "",
                "cumulativeVolumeShare": round(cumulative, 4) if share != "" else "",
                "suppliers": int(row["suppliers"]),
                "maxPriceCoveragePct": row["maxPriceCoveragePct"],
                "weightedReferenceMaxPrice": weighted,
            })
    return pd.DataFrame(rows)


def build_pricing_scenarios(inputs: AnalyticsInputs, enriched: pd.DataFrame) -> pd.DataFrame:
    df = enriched
    rows: list[dict[str, Any]] = []
    scenario_names = {
        0.0: "reference_at_max_aip",
        0.05: "discount_5pct",
        0.10: "discount_10pct",
        0.20: "discount_20pct",
        0.30: "discount_30pct",
        0.40: "discount_40pct",
    }

    for molecule in MOLECULE_ORDER:
        mol = df[df["productMolecule"] == molecule]
        qualifying = mol[mol["maxPrice"].notna() & mol["packsSoldLast12m"].apply(parse_volume).notna()].copy()
        qualifying["_volume"] = qualifying["packsSoldLast12m"].apply(parse_volume)
        period_warning = _volume_period_warning(mol)
        for discount in [0.0, *DISCOUNT_PCTS]:
            scenario_rows = qualifying
            ref_total = float((scenario_rows["maxPrice"] * scenario_rows["_volume"]).sum()) if not scenario_rows.empty else 0.0
            unit_factor = 1.0 - discount
            scenario_total = ref_total * unit_factor if ref_total else ""
            rows.append({
                "recordType": "molecule_summary",
                "molecule": molecule,
                "noticeId": "",
                "itemNumber": "",
                "productName": "",
                "scenarioName": scenario_names[discount],
                "discountPct": int(discount * 100) if discount else 0,
                "referenceGrossValue": ref_total if discount == 0.0 else "",
                "scenarioUnitPrice": "",
                "scenarioGrossValue": scenario_total,
                "priceSource": "mixed" if molecule in {"Axitinib", "Anagrelide"} else "tender_document",
                "maxPriceEffectiveDate": "2026-08-03" if molecule in {"Axitinib", "Anagrelide"} else "",
                "warning": period_warning,
            })
        for _, pack in qualifying.iterrows():
            ref = float(pack["maxPrice"] * pack["_volume"])
            for discount in [0.0, *DISCOUNT_PCTS]:
                unit = float(pack["maxPrice"]) * (1.0 - discount)
                rows.append({
                    "recordType": "pack_row",
                    "molecule": molecule,
                    "noticeId": pack["noticeId"],
                    "itemNumber": pack["itemNumber"],
                    "productName": pack["productName"],
                    "scenarioName": scenario_names[discount],
                    "discountPct": int(discount * 100) if discount else 0,
                    "referenceGrossValue": ref if discount == 0.0 else "",
                    "scenarioUnitPrice": round(unit, 2),
                    "scenarioGrossValue": ref * (1.0 - discount),
                    "priceSource": pack.get("maxPriceSource") or "tender_document",
                    "maxPriceEffectiveDate": pack.get("maxPriceEffectiveDate") or "",
                    "warning": pack.get("dmpTemporalWarning") or period_warning,
                })
    return pd.DataFrame(rows)


def _volume_period_warning(mol: pd.DataFrame) -> str:
    labels = set(mol.get("volumePeriodLabel", pd.Series(dtype=str)).dropna().astype(str))
    if any("PAKNINGER" in label for label in labels):
        return "Mixed volume periods: calendar-year proxy vs explicit last-12-months where documented."
    if (mol["noticeId"] == "434619-2026").any():
        return "2601c volumes are documented last-12-month sales; other rows may use calendar-year proxies."
    return ""
