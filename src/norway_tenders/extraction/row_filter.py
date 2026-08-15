from __future__ import annotations

import re
from dataclasses import dataclass

from norway_tenders.matching.matcher import load_molecule_config
from norway_tenders.models import MoleculeMatch, PackRecord

ACCEPTED_ATC: dict[str, frozenset[str]] = {
    "Axitinib": frozenset({"L01EK01", "L01XE17"}),
    "Everolimus": frozenset({"L01EG02", "L04AH02"}),
    "Lenalidomide": frozenset({"L04AX04"}),
    "Anagrelide": frozenset({"L01XX35", "B01AC14"}),
    "Paliperidone": frozenset({"N05AX13"}),
}

ACCEPTED_NAMES: dict[str, tuple[str, ...]] = {
    "Axitinib": ("axitinib",),
    "Everolimus": ("everolimus",),
    "Lenalidomide": ("lenalidomide", "lenalidomid"),
    "Anagrelide": ("anagrelide", "anagrelid"),
    "Paliperidone": ("paliperidone", "paliperidon"),
}

EXCLUSION_PATTERNS: dict[str, tuple[str, ...]] = {
    "Everolimus": (
        r"\bmykofenol",
        r"\bmycophenol",
        r"\bL04AA06\b",
    ),
}

SUPPORTING_BRANDS: dict[str, tuple[str, ...]] = {
    "Axitinib": ("inlyta",),
}


@dataclass(frozen=True)
class RowFilterResult:
    accepted: bool
    match: MoleculeMatch | None
    rejection_reason: str
    matched_term: str
    evidence_level: str
    raw_atc: str
    raw_molecule: str
    raw_product_name: str


def _row_text(pack: PackRecord) -> str:
    substance = str(pack.provenance.raw_values.get("active_substance", "") or "")
    return f"{pack.product_name} {substance} {pack.atc_code}".strip()


def _find_name_in_text(text: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            return name
    return None


def _find_atc_in_text(text: str, atcs: frozenset[str]) -> str | None:
    for atc in atcs:
        if re.search(rf"\b{re.escape(atc)}\b", text, re.IGNORECASE):
            return atc
    return None


def _has_brand_only(text: str, brands: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(b)}\b", text, re.IGNORECASE) for b in brands)


def _other_target_evidence(text: str, target_molecule: str) -> bool:
    config = load_molecule_config()
    for molecule, spec in config.items():
        if molecule == target_molecule:
            continue
        if _find_name_in_text(text, spec.names):
            return True
        if _find_atc_in_text(text, frozenset(spec.atc_codes)):
            return True
    return False


def evaluate_pack_for_target(pack: PackRecord, target_molecule: str) -> RowFilterResult:
    text = _row_text(pack)
    raw_atc = pack.atc_code or ""
    raw_substance = str(pack.provenance.raw_values.get("active_substance", "") or "")
    raw_product = pack.product_name or ""

    if not pack.item_number and not raw_product:
        return RowFilterResult(
            accepted=False, match=None, rejection_reason="empty_row",
            matched_term="", evidence_level="no_confirmation",
            raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
        )
    if raw_product.casefold() in {"varebetegnelse", "produktnavn", "varenavn"}:
        return RowFilterResult(
            accepted=False, match=None, rejection_reason="header_or_template_row",
            matched_term="", evidence_level="no_confirmation",
            raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
        )
    if not pack.item_number or not str(pack.item_number).strip().isdigit():
        if not _find_name_in_text(text, ACCEPTED_NAMES[target_molecule]):
            return RowFilterResult(
                accepted=False, match=None, rejection_reason="missing_item_number",
                matched_term="", evidence_level="no_confirmation",
                raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
            )

    names = ACCEPTED_NAMES[target_molecule]
    atcs = ACCEPTED_ATC[target_molecule]
    name_hit = _find_name_in_text(text, names)
    atc_hit = _find_atc_in_text(text, atcs) or (
        raw_atc.upper() if raw_atc.upper() in atcs else None
    )

    for pattern in EXCLUSION_PATTERNS.get(target_molecule, ()):
        if re.search(pattern, text, re.IGNORECASE) and not (name_hit or atc_hit):
            return RowFilterResult(
                accepted=False, match=None,
                rejection_reason="mycophenolic_acid_excluded",
                matched_term="", evidence_level="no_confirmation",
                raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
            )

    if target_molecule == "Paliperidone":
        if raw_atc and raw_atc.upper() != "N05AX13" and not name_hit:
            return RowFilterResult(
                accepted=False, match=None, rejection_reason="unrelated_atc_in_workbook",
                matched_term="", evidence_level="no_confirmation",
                raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
            )
        if not name_hit and not atc_hit:
            return RowFilterResult(
                accepted=False, match=None, rejection_reason="unrelated_molecule_in_workbook",
                matched_term="", evidence_level="no_confirmation",
                raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
            )

    if target_molecule == "Axitinib":
        brands = SUPPORTING_BRANDS["Axitinib"]
        if _has_brand_only(text, brands) and not name_hit and not atc_hit:
            return RowFilterResult(
                accepted=False, match=None, rejection_reason="brand_only_insufficient",
                matched_term="inlyta", evidence_level="brand_only",
                raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
            )

    if not name_hit and not atc_hit:
        if _other_target_evidence(text, target_molecule):
            return RowFilterResult(
                accepted=False, match=None, rejection_reason="unrelated_molecule_in_workbook",
                matched_term="", evidence_level="no_confirmation",
                raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
            )
        return RowFilterResult(
            accepted=False, match=None, rejection_reason="no_accepted_target_evidence",
            matched_term="", evidence_level="no_confirmation",
            raw_atc=raw_atc, raw_molecule=raw_substance, raw_product_name=raw_product,
        )

    if name_hit:
        evidence_level = "document_name_and_atc" if atc_hit else "document_name"
        match = MoleculeMatch(
            product_molecule=target_molecule,
            molecule_detected=True,
            molecule_variant=raw_product if name_hit in raw_product.casefold() else name_hit,
            detection_method="name_in_document",
            atc_code=atc_hit or raw_atc or "",
            matched_text=name_hit,
        )
        # Preserve exact source spelling for variant when found in product fields
        for variant in names:
            m = re.search(rf"\b({re.escape(variant)})\b", text, re.IGNORECASE)
            if m:
                match.molecule_variant = m.group(1)
                break
        return RowFilterResult(
            accepted=True,
            match=match,
            rejection_reason="",
            matched_term=name_hit,
            evidence_level=evidence_level,
            raw_atc=raw_atc,
            raw_molecule=raw_substance,
            raw_product_name=raw_product,
        )

    assert atc_hit
    match = MoleculeMatch(
        product_molecule=target_molecule,
        molecule_detected=False,
        molecule_variant="",
        detection_method="atc_in_document",
        atc_code=atc_hit,
        matched_text=atc_hit,
    )
    return RowFilterResult(
        accepted=True,
        match=match,
        rejection_reason="",
        matched_term=atc_hit,
        evidence_level="document_atc",
        raw_atc=raw_atc,
        raw_molecule=raw_substance,
        raw_product_name=raw_product,
    )
