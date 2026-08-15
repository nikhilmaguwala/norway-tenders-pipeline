from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from norway_tenders.matching.matcher import load_molecule_config

BRAND_ONLY_TERMS = {"inlyta", "revlimid", "afinitor", "xeplion", "invega"}


@dataclass(frozen=True)
class SearchTerm:
    molecule: str
    term: str
    match_type: str  # name, atc, brand_discovery


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value.casefold())


def build_search_terms() -> list[SearchTerm]:
    config = load_molecule_config()
    terms: list[SearchTerm] = []
    for molecule, spec in config.items():
        for name in spec.names:
            terms.append(SearchTerm(molecule, name, "name"))
        for atc in spec.atc_codes:
            terms.append(SearchTerm(molecule, atc, "atc"))
        for brand in spec.discovery_brands:
            terms.append(SearchTerm(molecule, brand, "brand_discovery"))
    return terms


def classify_match(term: SearchTerm, cell_text: str, row_text: str) -> str:
    if term.match_type == "brand_discovery":
        return "brand_only"
    if term.match_type == "name":
        has_atc = any(
            re.search(rf"\b{re.escape(t.term)}\b", row_text, re.I)
            for t in build_search_terms()
            if t.match_type == "atc" and t.molecule == term.molecule
        )
        if has_atc:
            return "name_and_atc"
        return "name"
    return "atc"


def term_in_text(term: str, text: str) -> bool:
    return bool(re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE))


def sheet_layout_hint(sheet_name: str, headers: str, has_pack_cols: bool) -> str:
    h = _fold(f"{sheet_name} {headers}")
    if "prisskjema" in h or has_pack_cols:
        return "pack-bearing price schedule"
    if "virkestoff" in h or "lot" in h:
        return "molecule/lot list"
    if "omfang" in h:
        return "historical scope table"
    if "krav" in h:
        return "requirements table"
    if any(x in h for x in ("instruks", "veiledning", "mal", "template")):
        return "instruction/template sheet"
    return "unknown layout"
