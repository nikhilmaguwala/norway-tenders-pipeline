# Data dictionary

Canonical output: `data/processed/output.csv` — **41 rows**, **28 columns**, UTF-8 CSV. One row per accepted pack/procedure combination.

| Column | Type | Description | Meaning when blank |
|--------|------|-------------|-------------------|
| `noticeId` | string | TED notice identifier (e.g. `434619-2026`) | — |
| `tenderRef` | string | Buyer procedure reference (e.g. `2601c`, `LIS 2234`) | — |
| `title` | string | Notice title from TED or document context | Unavailable in source |
| `country` | string | ISO country code (`NO`) | — |
| `buyer` | string | Contracting authority name | — |
| `productMolecule` | string | Normalised target molecule name | — |
| `moleculeDetected` | boolean string | `True` when explicit molecule name evidence supports the row | `False` for ATC-only or brand-assisted matches |
| `moleculeVariant` | string | Spelling variant detected in source (e.g. Norwegian form) | No variant recorded |
| `detectionMethod` | string | Evidence channel: `name_in_document`, `atc_in_document`, `name_in_notice`, `atc_in_notice` | Row rejected if blank |
| `atcCode` | string | WHO ATC code when present | ATC not stated for row |
| `itemNumber` | string | Hospital item / varenummer from price schedule | Item number not in source |
| `productName` | string | Product description from tender document | — |
| `strength` | string | Normalised strength (e.g. `25 mg`) | Strength not stated |
| `packSize` | string | Pack presentation | Pack size not stated |
| `supplier` | string | Listed supplier in price schedule | Supplier not listed or not extracted |
| `maxPrice` | number | Maximum administrative invoice price (NOK per pack) from tender document or validated DMP reference | No defensible maximum price |
| `packsSoldLast12m` | number | Observed source pack volume (12-month or labelled proxy period) | Volume unavailable in collected documents |
| `estimatedValue` | number | Accepted dedicated notice-level contract estimate (NOK), deduplicated by notice | No accepted dedicated estimate |
| `awardedValue` | number | Value from verified award notice | Award value not verified |
| `awardedSupplier` | string | Supplier from verified award | Award supplier not verified |
| `currency` | string | Currency code (`NOK`) | — |
| `noticeType` | string | Readable notice classification (e.g. Competition notice) | — |
| `status` | string | Procedure status when published (`open`, etc.) | Status not published |
| `publicationDate` | date | Notice publication date (ISO) | Date not available |
| `contractStart` | date | Contract start when stated | Not stated |
| `procedureType` | string | Procedure type (e.g. Open procedure) | Not classified |
| `sourceDocument` | string | Filename of originating workbook or PDF | — |
| `sourceUrl` | string | TED notice URL | URL not recorded |

## Semantics for key fields

### `moleculeDetected`

`True` only when explicit molecule **name** evidence supports inclusion. ATC-only matches (Axitinib) remain `False` with `detectionMethod = atc_in_document`.

### `detectionMethod`

Records the strongest evidence used. `name_in_document` is preferred over ATC-only paths.

### `maxPrice`

Maximum AIP reference ceiling — **not** offered bid price, not GIP. Sources: tender document (19 rows) or DMP current reference (7 rows) per `data/processed/dmp_price_join_audit.csv`.

### `packsSoldLast12m`

- **Blank:** volume unavailable in collected documents (Anagrelide, Everolimus).
- **0:** explicit observed zero in source (Paliperidone has seven such rows).
- **Positive number:** observed packs in the stated period.

### `estimatedValue`

A **notice-level** contract estimate from accepted TED XML — not a pack-level price or volume-derived value.

- The same value is repeated on every pack row belonging to that accepted notice.
- **Deduplicate by `noticeId`** before any aggregation (sum, average, ranking input).
- **Never sum `estimatedValue` directly across pack rows** — that double-counts the notice total.
- Only **dedicated molecule-specific** estimates are accepted (Lenalidomide NOK 320m, Anagrelide NOK 10m in this dataset).
- **Umbrella and multi-molecule** notice totals are rejected (`data/processed/notice_value_audit.csv`).

### `awardedValue` / `awardedSupplier`

Left blank — award outcomes are not verified in this dataset.

### `noticeType` / `procedureType`

Mapped from TED and document metadata for readability.

### `sourceDocument` / `sourceUrl`

Provenance pointers; detailed pack-level evidence is in `data/processed/pack_evidence.csv`.
