# Methodology

Technical overview of the Norwegian pharmaceutical tender pipeline. All stages are deterministic and can be rebuilt from collected source documents, cached discovery metadata, and configuration.

## Discovery

TED Search API queries are built per target molecule using English and Norwegian names, ATC codes, brand names, LIS/tender references, and buyer names (`Sykehusinnkjøp HF`). Results are cached under `data/cache/ted_search/` and summarised in `data/discovery/review_candidates.csv`. Candidates are classified for human review before document collection.

## Source collection

Mercell procurement pages and TED notice XML provide document links. Direct attachment URLs that require authentication are recorded in `data/discovery/document_access.csv`. Those files are downloaded manually and registered in `data/seeds/sources.csv` with SHA-256 fingerprints.

## Document validation

Local files are validated for extension, file signature, workbook readability, and PDF text extractability (`data/discovery/local_file_validation.csv`). A source inventory links each accepted workbook or PDF to notice, molecule, and parser layout (`data/discovery/parser_layout_report.csv`).

## Extraction

Parsers handle heterogeneous Excel layouts: header rows, merged cells, and column naming differ between dedicated molecule tenders and umbrella schedules. Extracted rows retain item numbers, product text, strength, pack size, supplier, maximum AIP, and historical pack sales where present.

## Molecule and ATC matching

Rows pass through an evidence hierarchy:

1. Molecule name and ATC both present in the row
2. Molecule name in document
3. Validated ATC in document
4. Brand name with validated ATC
5. Uncertain or unrelated rows rejected

Rejected rows are logged in `data/processed/row_filter_audit.csv`. Axitinib is accepted on ATC L01EK01 and Inlyta brand evidence where explicit INN text is absent.

## Normalisation

Molecule names, strengths, and pack sizes are standardised. Supplier placeholders (`#N/A`, grossist labels) are removed. Notice codes map to readable `noticeType` and `procedureType` values. Explicit numeric zero is preserved; missing values remain blank.

## DMP maximum-price enrichment

The Direktoratet for medisinske produkter workbook (effective **2026-08-03**) joins on exact `itemNumber` matches only. Tender-document maximum AIP takes precedence over DMP current reference. Offered GIP is never mapped to `maxPrice`. Join outcomes are audited in `data/processed/dmp_price_join_audit.csv`.

## Notice lifecycle

PIN, competition, revision, award, and VEAT notices sharing a procedure key are linked in `data/processed/lifecycle_linkage.csv` so pack rows are not duplicated across lifecycle stages.

## Notice values

Dedicated molecule estimates from TED XML may populate `estimatedValue` once per `noticeId`. The value is notice-level: it is repeated on every pack row for that notice and must be deduplicated by `noticeId` before aggregation — never summed across pack rows. Umbrella or multi-molecule totals, historical turnover, and confidential unpublished values are rejected (`data/processed/notice_value_audit.csv`). Listed supplier is not treated as awarded supplier.

## Validation and rebuild

Phase 5 validation gates enforce row counts, column order, detection semantics, price precedence, and lifecycle rules. `python -m norway_tenders.cli build --offline` rebuilds `data/processed/output.csv` from seeds and caches without network access. `python -m norway_tenders.cli analyse` regenerates tables and static charts from the canonical CSV.
