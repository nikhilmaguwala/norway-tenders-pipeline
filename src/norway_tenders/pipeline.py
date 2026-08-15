from __future__ import annotations

import logging
from pathlib import Path

from norway_tenders.discovery.seeds import load_seed_sources, resolve_seed_path, seeds_for_notice
from norway_tenders.discovery.candidates import DiscoveryRunResult, run_discovery
from norway_tenders.discovery.ted import discover_ted_notices
from norway_tenders.parsers.ted_xml import enrich_notice_from_xml, extract_lot_rows, match_notice_to_molecule
from norway_tenders.matching.evidence import is_pharmaceutical_evidence
from norway_tenders.matching.matcher import match_pack, match_text
from norway_tenders.models import DocumentRecord, NoticeRecord, OutputRow
from norway_tenders.normalise.lifecycle import (
    build_notice_row,
    build_pack_row,
    is_competition_notice,
    merge_lifecycle_rows,
    validate_output,
    write_output_csv,
)
from norway_tenders.parsers.lis_excel import (
    parse_kravspec_omfang,
    parse_kravspec_product_requirements,
    parse_lis_prisskjema,
)
from norway_tenders.parsers.pdf_parser import extract_pdf_text
from norway_tenders.retrieval.downloader import fetch_documents, fetch_ted_xml
from norway_tenders.settings import OUTPUT_CSV, RAW_DIR

logger = logging.getLogger(__name__)

MERCCELL_DOC_PATTERNS = {
    "LIS2234": [
        (
            "LIS 2234 Vedlegg 03 Prisskjema.xlsx",
            "https://www.mercell.com/m/file/GetFile.ashx?id=158259205&version=0",
        ),
        (
            "LIS 2234 Vedlegg 02 Kravspesifikasjon.xlsx",
            "https://www.mercell.com/m/file/GetFile.ashx?id=158259201&version=0",
        ),
    ],
    "LIS2234B": [
        (
            "LIS 2234b Vedlegg 03 Prisskjema.xlsx",
            "https://www.mercell.com/m/file/GetFile.ashx?id=159475531&version=0",
        ),
    ],
}


def discover(*, offline: bool = False, refresh: bool = False) -> DiscoveryRunResult:
    return run_discovery(offline=offline, refresh=refresh)


def audit_discovery(*, offline: bool = False, refresh: bool = False):
    from norway_tenders.discovery.phase4b import run_phase4b

    return run_phase4b(offline=offline, refresh=refresh)


def discover_notices(*, offline: bool = False, refresh: bool = False) -> list[NoticeRecord]:
    return discover_ted_notices(offline=offline, refresh=refresh)


def fetch(*, offline: bool = False, refresh: bool = False) -> list[DocumentRecord]:
    notices = discover_notices(offline=True)
    seeds = load_seed_sources()
    documents: list[DocumentRecord] = []
    for notice in notices:
        documents.extend(seeds_for_notice(notice, seeds))
        ref = (notice.tender_ref or notice.title).upper().replace(" ", "")
        for key, files in MERCCELL_DOC_PATTERNS.items():
            if key in ref or key.replace("B", "") in ref:
                for filename, url in files:
                    documents.append(
                        DocumentRecord(
                            notice_id=notice.notice_id,
                            url=url,
                            filename=filename,
                        )
                    )
    return fetch_documents(documents, offline=offline, refresh=refresh)


def build(*, offline: bool = False, refresh: bool = False) -> list[OutputRow]:
    notices = discover_notices(offline=offline, refresh=refresh)
    seeds = load_seed_sources()
    rows: list[OutputRow] = []

    for notice in notices:
        xml = ""
        try:
            xml = fetch_ted_xml(notice.notice_id, offline=offline)
            notice = enrich_notice_from_xml(notice, xml)
        except Exception as exc:
            logger.warning("XML fetch failed for %s: %s", notice.notice_id, exc)

        matches = [
            (n, m) for n, m in match_notice_to_molecule(notice) if is_pharmaceutical_evidence(n, m)
        ]
        lot_rows = extract_lot_rows(notice, xml) if xml else []
        if not matches and not lot_rows:
            continue

        pack_rows_added = False
        seed_docs = seeds_for_notice(notice, seeds)
        ref = (notice.tender_ref or "").upper().replace(" ", "")
        extra_docs: list[DocumentRecord] = []
        for key, files in MERCCELL_DOC_PATTERNS.items():
            if key in ref or (key.endswith("2234") and "2234" in ref):
                for filename, url in files:
                    extra_docs.append(
                        DocumentRecord(notice_id=notice.notice_id, url=url, filename=filename)
                    )

        all_docs = seed_docs + extra_docs
        local_dir = RAW_DIR / notice.notice_id

        def _resolve_doc_path(doc: DocumentRecord) -> Path | None:
            if doc.local_path:
                path = Path(doc.local_path)
                if path.exists():
                    return path
            seed_path = resolve_seed_path(doc.filename)
            if seed_path:
                return seed_path
            if doc.filename:
                raw_path = local_dir / doc.filename
                if raw_path.exists():
                    return raw_path
            return None

        for doc in all_docs:
            local_path = _resolve_doc_path(doc)
            if local_path and local_path.read_bytes()[:2] == b"PK":
                if "prisskjema" in doc.filename.casefold() and is_competition_notice(
                    notice.notice_type
                ):
                    for _, match in matches:
                        packs = parse_lis_prisskjema(
                            local_path, source_url=doc.url or str(local_path)
                        )
                        for pack in packs:
                            doc_match = match_pack(pack, context="document") or match
                            if doc_match.product_molecule != match.product_molecule:
                                continue
                            rows.append(
                                build_pack_row(
                                    notice,
                                    doc_match,
                                    pack,
                                    source_document=doc.filename,
                                    source_url=doc.url or notice.source_url,
                                )
                            )
                            pack_rows_added = True
                elif "kravspesifikasjon" in doc.filename.casefold():
                    evidence = parse_kravspec_omfang(local_path, source_url=doc.url or "")
                    if evidence:
                        logger.info(
                            "Historical turnover evidence for %s: %s (not mapped to estimatedValue)",
                            notice.notice_id,
                            evidence,
                        )
                    product_evidence = parse_kravspec_product_requirements(
                        local_path, source_url=doc.url or ""
                    )
                    if product_evidence.price_weighting_percent is not None:
                        logger.info(
                            "Product requirement evidence for %s: %s (internal only)",
                            notice.notice_id,
                            product_evidence,
                        )

        # Also scan cached raw directory
        if local_dir.exists():
            for xlsx in local_dir.glob("*.xlsx"):
                if "prisskjema" in xlsx.name.casefold() and is_competition_notice(
                    notice.notice_type
                ):
                    try:
                        packs = parse_lis_prisskjema(xlsx, source_url=notice.source_url)
                        for _, match in matches:
                            for pack in packs:
                                doc_match = match_pack(pack, context="document") or match
                                rows.append(
                                    build_pack_row(
                                        notice,
                                        doc_match,
                                        pack,
                                        source_document=xlsx.name,
                                        source_url=notice.source_url,
                                    )
                                )
                                pack_rows_added = True
                    except ValueError as exc:
                        logger.warning("Skip invalid xlsx %s: %s", xlsx.name, exc)

            for pdf in local_dir.glob("*.pdf"):
                text = extract_pdf_text(pdf)
                if text:
                    doc_match = match_text(text, context="document")
                    if doc_match:
                        for notice_match in matches:
                            if doc_match.product_molecule == notice_match[1].product_molecule:
                                row = build_notice_row(notice, doc_match)
                                row.source_document = pdf.name
                                rows.append(row)

        if lot_rows:
            rows.extend(lot_rows)
            pack_rows_added = True
        elif not pack_rows_added:
            for _, match in matches:
                rows.append(build_notice_row(notice, match))

    rows = merge_lifecycle_rows(rows)
    validation = validate_output(rows)
    for err in validation["errors"]:
        logger.error("Validation: %s", err)
    logger.info("Build stats: %s", validation["stats"])
    write_output_csv(rows, OUTPUT_CSV)
    return rows


def run(*, offline: bool = False, refresh: bool = False) -> list[OutputRow]:
    discover(offline=offline, refresh=refresh)
    fetch(offline=offline, refresh=refresh)
    return build(offline=offline, refresh=refresh)
