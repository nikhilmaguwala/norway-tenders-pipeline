from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
SEEDS_DIR = DATA_DIR / "seeds"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"
CHARTS_DIR = REPORTS_DIR / "charts"
TABLES_DIR = REPORTS_DIR / "tables"
ANALYTICS_SUMMARY_JSON = REPORTS_DIR / "analytics_summary.json"
ANALYTICS_NOTES_MD = REPORTS_DIR / "analytics_notes.md"
CHART_QA_MD = REPORTS_DIR / "chart_qa.md"

MOLECULES_CONFIG = CONFIG_DIR / "molecules.yml"
SOURCES_SEED = SEEDS_DIR / "sources.csv"
OUTPUT_CSV = PROCESSED_DIR / "output.csv"
DISCOVERY_DIR = DATA_DIR / "discovery"
REVIEW_CANDIDATES_CSV = DISCOVERY_DIR / "review_candidates.csv"
DISCOVERY_LOG = DISCOVERY_DIR / "discovery.log"
DISCOVERY_SUMMARY = DISCOVERY_DIR / "discovery_summary.json"
AXITINIB_GAP_REPORT = DISCOVERY_DIR / "axitinib_gap_report.csv"
AUDITED_CANDIDATES_CSV = DISCOVERY_DIR / "audited_candidates.csv"
PROCEDURE_SUMMARY_CSV = DISCOVERY_DIR / "procedure_summary.csv"
DOCUMENT_ACCESS_CSV = DISCOVERY_DIR / "document_access.csv"
PHASE4B_REPORT = DISCOVERY_DIR / "phase4b_report.json"
DOCUMENT_ACCESS_CACHE_DIR = CACHE_DIR / "document_access"
TED_SEARCH_CACHE_DIR = CACHE_DIR / "ted_search"
DISCOVERY_CACHE = CACHE_DIR / "discovered_notices.json"
MANIFEST_CACHE = CACHE_DIR / "document_manifest.json"

USER_AGENT = "norway-tenders-pipeline/0.1 (research; +https://github.com/nikhilmaguwala/norway-tenders-pipeline)"
REQUEST_DELAY_SECONDS = 1.5
TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"

OUTPUT_COLUMNS: list[str] = [
    "noticeId",
    "tenderRef",
    "title",
    "country",
    "buyer",
    "productMolecule",
    "moleculeDetected",
    "moleculeVariant",
    "detectionMethod",
    "atcCode",
    "itemNumber",
    "productName",
    "strength",
    "packSize",
    "supplier",
    "maxPrice",
    "packsSoldLast12m",
    "estimatedValue",
    "awardedValue",
    "awardedSupplier",
    "currency",
    "noticeType",
    "status",
    "publicationDate",
    "contractStart",
    "procedureType",
    "sourceDocument",
    "sourceUrl",
]

ALLOWED_MOLECULES = {
    "Axitinib",
    "Everolimus",
    "Lenalidomide",
    "Anagrelide",
    "Paliperidone",
}

DETECTION_METHODS = {
    "name_in_document",
    "atc_in_document",
    "name_in_notice",
    "atc_in_notice",
}

REVIEW_CANDIDATE_COLUMNS: list[str] = [
    "targetMolecule",
    "queryUsed",
    "matchedTerm",
    "detectionMethod",
    "noticeId",
    "tenderRef",
    "title",
    "buyer",
    "publicationDate",
    "noticeType",
    "status",
    "lifecycleStage",
    "estimatedValue",
    "currency",
    "noticeUrl",
    "documentUrls",
    "proposedDecision",
    "decisionReason",
    "language",
    "possibleDuplicateOf",
]

LIS_BUYER_NAMES = (
    "Sykehusinnkjøp HF",
    "Sykehusinnkjøp",
    "Sykehusinnkjop HF",
    "Sykehusinnkjop",
)
