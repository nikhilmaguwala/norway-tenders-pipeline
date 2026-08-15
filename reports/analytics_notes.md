# Analytics notes (Phase 6)

## Coverage statement

Analytics cover five target molecules drawn from six canonical Norwegian hospital pharmaceutical procedures in a small take-home dataset. Results describe observed source documents only and do not represent full Norwegian market coverage, market share, win probability, or optimal bid pricing.

Extraction as-of date: **2026-08-14**.

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

Component weights: {"observableScaleScore": 0.3, "contestabilityScore": 0.25, "portfolioBreadthScore": 0.2, "timingActionabilityScore": 0.15, "evidenceConfidenceScore": 0.1}.

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

Paliperidone has four listed suppliers in collected price schedules, but only Amdipharm and Janssen-Cilag have positive observed volume. Orifarm and Zentiva appear with explicit zero observed volume.

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

- **Paliperidone**: 79.97 (High)
- **Lenalidomide**: 67.77 (High)
- **Axitinib**: 47.25 (Medium)
- **Anagrelide**: 32.45 (Low)
- **Everolimus**: 29.08 (Low)

## Data-driven recommendation

- **Primary opportunity**: Paliperidone
- **Secondary opportunity**: Lenalidomide
- **Watchlist / evidence-gap candidates**: Axitinib, Anagrelide, Everolimus

### Next evidence before bid/no-bid

Use the `recommendedNextAction` field in `opportunity_scorecard.csv` per molecule. Awards remain outside reliable coverage; do not treat listed suppliers as winners.
