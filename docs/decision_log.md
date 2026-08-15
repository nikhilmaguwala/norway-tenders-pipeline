# Decision log

Concise record of non-obvious analytical and data-quality decisions.

## Molecule evidence

- **Axitinib ATC/brand acceptance:** Pack rows accepted on ATC L01EK01 and Inlyta brand where explicit Axitinib INN text is absent. Evidence confidence capped at moderate; oncology umbrella context documented.
- **Folder names not used as evidence:** Directory or archive folder names never qualify a row; only document content and notice metadata count.

## Price semantics

- **Exact DMP matching only:** DMP maximum AIP applies only when `itemNumber` matches exactly and validation passes.
- **Tender price precedence:** Tender-document `Maks AIP` overrides DMP current reference when both exist.
- **GIP rejection:** Offered gross invoice price (GIP) is never mapped to `maxPrice`.
- **Current-reference warning:** DMP-enriched prices dated 2026-08-03 are current administrative references, not tender-time prices.

## Notice values

- **Umbrella-value rejection:** Multi-molecule procedure totals (e.g. Axitinib NOK 3.2bn oncology umbrella, Everolimus NOK 128m combined lot, Paliperidone NOK 14.67m seven-medicine VEAT) are not allocated to individual molecules.
- **Historical turnover rejection:** Kravspesifikasjon historical max-AIP turnover is logged but not mapped to `estimatedValue`.
- **Unverified award rejection:** `awardedValue` and `awardedSupplier` remain blank without verified award disclosure.

## Volume and suppliers

- **Explicit zero preservation:** `packsSoldLast12m = 0` means an observed zero in source documents, not missing demand.
- **Missing volume preservation:** Blank volume means unavailable in collected documents, not zero demand.
- **Listed supplier ≠ winner:** Supplier names in price schedules indicate listing only.

## Deduplication

- **Lifecycle deduplication:** Procedure keys link lifecycle notices; pack row keys prevent duplicate acceptance across stages.
- **Notice estimate deduplication:** `estimatedValue` is deduplicated by `noticeId` before molecule-level analytics.
