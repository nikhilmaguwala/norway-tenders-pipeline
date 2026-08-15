from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from norway_tenders.settings import DETECTION_METHODS


@dataclass
class Provenance:
    source_url: str = ""
    filename: str = ""
    sheet: str = ""
    page: int | None = None
    row: int | None = None
    raw_values: dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime | None = None
    sha256: str = ""


@dataclass
class NoticeRecord:
    notice_id: str = ""
    tender_ref: str = ""
    title: str = ""
    country: str = "NO"
    buyer: str = ""
    notice_type: str = ""
    status: str = ""
    publication_date: str = ""
    contract_start: str = ""
    procedure_type: str = ""
    source_url: str = ""
    description: str = ""
    estimated_value: float | None = None
    currency: str = "NOK"
    cpv_codes: list[str] = field(default_factory=list)
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class DocumentRecord:
    notice_id: str
    url: str
    filename: str
    local_path: str = ""
    content_type: str = ""
    sha256: str = ""
    retrieved_at: datetime | None = None
    download_error: str = ""
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class PackRecord:
    item_number: str = ""
    product_name: str = ""
    strength: str = ""
    pack_size: int | float | None = None
    atc_code: str = ""
    supplier: str = ""
    max_price: float | None = None
    offered_gip: float | None = None
    packs_sold_last_12m: float | None = None
    packs_year: int | None = None
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class MoleculeMatch:
    product_molecule: str
    molecule_detected: bool
    molecule_variant: str = ""
    detection_method: str = ""
    atc_code: str = ""
    matched_text: str = ""

    def __post_init__(self) -> None:
        if self.detection_method and self.detection_method not in DETECTION_METHODS:
            raise ValueError(f"Invalid detection method: {self.detection_method}")
        if self.molecule_detected and self.detection_method not in {
            "name_in_document",
            "name_in_notice",
        }:
            raise ValueError(
                "moleculeDetected=true requires name_in_document or name_in_notice"
            )


@dataclass
class TenderLevelEvidence:
    """Internal tender-level evidence; not written to output.csv."""

    price_weighting_percent: float | None = None
    min_discount_max_aip_percent: float | None = None
    offered_price_basis: str = ""
    equal_price_per_mg_within_formulation: bool | None = None
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class OutputRow:
    notice_id: str = ""
    tender_ref: str = ""
    title: str = ""
    country: str = "NO"
    buyer: str = ""
    product_molecule: str = ""
    molecule_detected: bool = False
    molecule_variant: str = ""
    detection_method: str = ""
    atc_code: str = ""
    item_number: str = ""
    product_name: str = ""
    strength: str = ""
    pack_size: int | float | None = None
    supplier: str = ""
    max_price: float | None = None
    packs_sold_last_12m: float | None = None
    estimated_value: float | None = None
    awarded_value: float | None = None
    awarded_supplier: str = ""
    currency: str = "NOK"
    notice_type: str = ""
    status: str = ""
    publication_date: str = ""
    contract_start: str = ""
    procedure_type: str = ""
    source_document: str = ""
    source_url: str = ""

    def row_key(self) -> str:
        procedure = normalise_procedure_key(self.tender_ref, self.notice_id)
        if self.item_number:
            return f"{procedure}|{self.product_molecule}|{self.item_number}"
        return f"{procedure}|{self.product_molecule}|{self.notice_id}"

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "noticeId": self.notice_id,
            "tenderRef": self.tender_ref,
            "title": self.title,
            "country": self.country,
            "buyer": self.buyer,
            "productMolecule": self.product_molecule,
            "moleculeDetected": self.molecule_detected,
            "moleculeVariant": self.molecule_variant,
            "detectionMethod": self.detection_method,
            "atcCode": self.atc_code,
            "itemNumber": self.item_number,
            "productName": self.product_name,
            "strength": self.strength,
            "packSize": self.pack_size,
            "supplier": self.supplier,
            "maxPrice": self.max_price,
            "packsSoldLast12m": self.packs_sold_last_12m,
            "estimatedValue": self.estimated_value,
            "awardedValue": self.awarded_value,
            "awardedSupplier": self.awarded_supplier,
            "currency": self.currency,
            "noticeType": self.notice_type,
            "status": self.status,
            "publicationDate": self.publication_date,
            "contractStart": self.contract_start,
            "procedureType": self.procedure_type,
            "sourceDocument": self.source_document,
            "sourceUrl": self.source_url,
        }


def normalise_procedure_key(tender_ref: str, notice_id: str) -> str:
    ref = (tender_ref or "").strip().upper()
    ref = ref.replace(" ", "")
    if ref:
        return ref
    return notice_id
