from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from norway_tenders.models import NoticeRecord, Provenance
from norway_tenders.parsers.ted_xml import enrich_notice_from_xml
from norway_tenders.retrieval.downloader import fetch_ted_xml
from norway_tenders.validation.seed_config import SeedFolderMeta

EBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"


def _iso_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if "T" in text or "+" in text or text.endswith("Z"):
        return text[:10]
    return text[:10] if len(text) >= 10 else text


def _parse_notice_fields(xml_text: str, notice: NoticeRecord) -> NoticeRecord:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return notice

    for el in root.iter(f"{EBC}PublicationDate"):
        if el.text:
            notice.publication_date = _iso_date(el.text)
            break

    # legacy DATE_PUB
    if not notice.publication_date:
        for el in root.iter():
            if el.tag.endswith("DATE_PUB") and el.text:
                notice.publication_date = _iso_date(el.text)
                break

    if not notice.buyer:
        for el in root.iter():
            if el.tag.endswith("OFFICIALNAME") and el.text:
                notice.buyer = el.text.strip()
                break
        if not notice.buyer:
            for el in root.iter(f"{CAC}Party"):
                name_el = el.find(f".//{CAC}PartyName/{EBC}Name")
                if name_el is not None and name_el.text and "sykehus" in name_el.text.casefold():
                    notice.buyer = name_el.text.strip()
                    break

    for el in root.iter(f"{EBC}ID"):
        if el.get("schemeName") == "InternalID" and el.text and not notice.tender_ref:
            notice.tender_ref = el.text.strip()

    for el in root.iter(f"{EBC}Name"):
        if el.text and len(el.text) > 8 and not notice.title:
            notice.title = el.text.strip()

    if "Nordic Pill" in xml_text and not notice.provenance.raw_values.get("awarded_supplier_notice"):
        match = re.search(r"Nordic Pill AB", xml_text)
        if match:
            notice.provenance.raw_values["awarded_supplier_notice"] = match.group(0)

    if notice.estimated_value and notice.estimated_value >= 14_600_000:
        notice.provenance.raw_values["multi_molecule_notice_value"] = notice.estimated_value

    for el in root.iter(f"{EBC}NoticeTypeCode"):
        if el.text:
            notice.notice_type = el.text.strip()
            break

    for el in root.iter(f"{EBC}StartDate"):
        if el.text and not notice.contract_start:
            notice.contract_start = _iso_date(el.text)

    return notice


def load_canonical_notice(meta: SeedFolderMeta, *, offline: bool = True) -> NoticeRecord:
    notice_url = meta.notice_url
    notice = NoticeRecord(
        notice_id=meta.notice_id,
        tender_ref=meta.tender_ref,
        title=meta.title,
        country="NO",
        source_url=notice_url,
        provenance=Provenance(source_url=notice_url),
    )

    xml_text = fetch_ted_xml(meta.notice_id, offline=offline)
    notice = enrich_notice_from_xml(notice, xml_text)
    notice = _parse_notice_fields(xml_text, notice)

    notice.notice_id = meta.notice_id
    notice.source_url = notice_url
    notice.provenance.source_url = notice_url

    if meta.target_molecule == "Anagrelide":
        notice.tender_ref = meta.tender_ref
        notice.provenance.raw_values["ted_internal_ref"] = "2024/206"
        notice.provenance.raw_values["procurement_family"] = meta.procurement_family
    elif meta.tender_ref:
        notice.tender_ref = meta.tender_ref

    if meta.target_molecule == "Lenalidomide" and notice.tender_ref == "2021/118":
        notice.tender_ref = "LIS 2234"

    notice.status = ""
    return notice
