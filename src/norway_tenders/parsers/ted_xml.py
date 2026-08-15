from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from norway_tenders.matching.matcher import load_molecule_config, match_text
from norway_tenders.models import MoleculeMatch, NoticeRecord, OutputRow
from norway_tenders.normalise.lifecycle import build_notice_row

NS = {"ted": "http://publications.europa.eu/resource/schema/ted/R2.0.9/publication"}
EBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"


def enrich_notice_from_xml(notice: NoticeRecord, xml_text: str) -> NoticeRecord:
    """Parse TED XML for title, description, values, dates, and Mercell links."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return notice

    # Legacy TED schema
    ref = root.find(".//ted:REFERENCE_NUMBER", NS)
    if ref is not None and ref.text:
        notice.tender_ref = notice.tender_ref or ref.text.strip()

    title = root.find(".//ted:OBJECT_CONTRACT/ted:TITLE/ted:P", NS)
    if title is not None and title.text:
        notice.title = title.text.strip()

    descr_nodes = root.findall(".//ted:SHORT_DESCR/ted:P", NS)
    if descr_nodes:
        notice.description = " ".join(n.text or "" for n in descr_nodes)

    ia_url = root.find(".//ted:IA_URL_GENERAL", NS)
    if ia_url is not None and ia_url.text:
        notice.provenance.source_url = ia_url.text.strip()

    val = root.find(".//ted:VAL_ESTIMATED_TOTAL", NS)
    if val is not None and val.text:
        try:
            notice.estimated_value = float(val.text.replace(",", "."))
            notice.currency = val.get("CURRENCY", notice.currency)
        except ValueError:
            pass

    date_start = root.find(".//ted:DATE_START", NS)
    if date_start is not None and date_start.text:
        notice.contract_start = date_start.text.strip()[:10]

    td = root.find(".//ted:TD_DOCUMENT_TYPE", NS)
    if td is not None and td.get("CODE"):
        notice.notice_type = td.get("CODE", notice.notice_type)

    pr = root.find(".//ted:PR_PROC", NS)
    if pr is not None and pr.get("CODE"):
        notice.procedure_type = pr.get("CODE", notice.procedure_type)

    # eForms UBL
    for name_el in root.iter(f"{EBC}Name"):
        if name_el.text and len(name_el.text) > 10:
            notice.title = notice.title or name_el.text.strip()

    for desc_el in root.iter(f"{EBC}Description"):
        if desc_el.text:
            notice.description = (notice.description + " " + desc_el.text).strip()

    return notice


def extract_lot_rows(notice: NoticeRecord, xml_text: str) -> list[OutputRow]:
    """Extract per-lot rows from TED XML where lots mention target molecules/ATCs."""
    rows: list[OutputRow] = []
    config = load_molecule_config()
    all_atcs = {atc: mol for mol, spec in config.items() for atc in spec.atc_codes}

    # Pattern: Lot 90 N05AX13 Paliperidon injection
    for match in re.finditer(
        r"Lot\s+(\d+)\s+([A-Z]\d{2}[A-Z]{2}\d{2})\s+([^\n<]+)",
        xml_text,
        re.IGNORECASE,
    ):
        lot_num, atc, product = match.groups()
        atc = atc.upper()
        if atc not in all_atcs:
            continue
        product_clean = product.strip()
        name_hit = re.search(
            r"\b(lenalidomid|lenalidomide|everolimus|anagrelid|anagrelide|paliperidon|paliperidone|axitinib)\b",
            product_clean,
            re.I,
        )
        if name_hit:
            mm = MoleculeMatch(
                product_molecule=all_atcs[atc],
                molecule_detected=True,
                molecule_variant=name_hit.group(0),
                detection_method="name_in_notice",
                atc_code=atc,
                matched_text=product_clean[:80],
            )
        else:
            mm = MoleculeMatch(
                product_molecule=all_atcs[atc],
                molecule_detected=False,
                molecule_variant=product_clean[:80],
                detection_method="atc_in_notice",
                atc_code=atc,
                matched_text=product_clean[:80],
            )
        row = build_notice_row(notice, mm)
        row.item_number = lot_num
        row.product_name = product.strip()
        row.atc_code = atc
        rows.append(row)

    # eForms axitinib-style: ATC Kode: L01EK01 · Generisk navn: Axitinib · Styrke: 5 mg
    for match in re.finditer(
        r"ATC\s*(?:Kode|code)?[:\s]*([A-Z]\d{2}[A-Z]{2}\d{2}).*?(?:navn|name)[:\s]*([^·\n<]+).*?(?:Styrke|strength)[:\s]*([^·\n<]+)",
        xml_text,
        re.IGNORECASE | re.DOTALL,
    ):
        atc, name, strength = match.groups()
        atc = atc.upper()
        if atc not in all_atcs:
            continue
        text = f"{name} {atc}"
        mm = match_text(text, context="notice")
        if not mm:
            continue
        row = build_notice_row(notice, mm)
        row.product_name = name.strip()
        row.strength = strength.strip()
        row.atc_code = atc
        rows.append(row)

    return rows


def match_notice_to_molecule(notice: NoticeRecord) -> list[tuple[NoticeRecord, MoleculeMatch]]:
    text = f"{notice.title} {notice.description}"
    match = match_text(text, context="notice")
    if match:
        return [(notice, match)]
    return []
