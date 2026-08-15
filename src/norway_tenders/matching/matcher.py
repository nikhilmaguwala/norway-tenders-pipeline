from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from norway_tenders.models import MoleculeMatch, PackRecord
from norway_tenders.settings import MOLECULES_CONFIG


@dataclass(frozen=True)
class MoleculeConfig:
    canonical_name: str
    names: tuple[str, ...]
    atc_codes: tuple[str, ...]
    discovery_brands: tuple[str, ...] = ()


def _fold_text(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def load_molecule_config(path: Path | None = None) -> dict[str, MoleculeConfig]:
    config_path = path or MOLECULES_CONFIG
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result: dict[str, MoleculeConfig] = {}
    for canonical, spec in raw["molecules"].items():
        atcs = tuple(spec.get("atc_current", []) + spec.get("atc_historical", []))
        result[canonical] = MoleculeConfig(
            canonical_name=canonical,
            names=tuple(spec["names"]),
            atc_codes=atcs,
            discovery_brands=tuple(spec.get("discovery_brands", [])),
        )
    return result


def parse_norwegian_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "–", "—", "N/A", "n/a"}:
        return None
    text = text.replace("\xa0", " ").replace(" ", "")
    text = text.replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text or text in {".", "-", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_pack_size(value: Any) -> int | float | None:
    """Parse pack size; rule: '21 ENPAC' -> 21."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"^(\d+)\s+ENPAC\b", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    num = parse_norwegian_number(text)
    if num is None:
        return None
    return int(num) if num == int(num) else num


def _find_name_match(
    text: str,
    molecules: dict[str, MoleculeConfig],
) -> tuple[str, str] | None:
    """Return (canonical_molecule, source_variant) if a configured name appears."""
    best: tuple[str, str, int] | None = None
    for mol, spec in molecules.items():
        for name in spec.names:
            pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
            hit = pattern.search(text)
            if hit and (best is None or len(hit.group(0)) > best[2]):
                best = (mol, hit.group(0), len(hit.group(0)))
    if best is None:
        return None
    return best[0], best[1]


def _find_atc_match(
    text: str,
    molecules: dict[str, MoleculeConfig],
) -> tuple[str, str] | None:
    """Return (canonical_molecule, atc_code) if a configured ATC appears."""
    for mol, spec in molecules.items():
        for atc in spec.atc_codes:
            if re.search(rf"\b{re.escape(atc)}\b", text, re.IGNORECASE):
                return mol, atc
    return None


def match_evidence(
    text: str,
    config: dict[str, MoleculeConfig] | None = None,
    *,
    context: str = "document",
) -> MoleculeMatch | None:
    """Evaluate name and ATC evidence independently; combine for output semantics."""
    if context not in {"document", "notice"}:
        raise ValueError(f"Invalid context: {context}")

    molecules = config or load_molecule_config()
    name_hit = _find_name_match(text, molecules)
    atc_hit = _find_atc_match(text, molecules)

    if not name_hit and not atc_hit:
        return None

    if name_hit:
        product_molecule, variant = name_hit
        atc_code = atc_hit[1] if atc_hit else ""
        method = "name_in_document" if context == "document" else "name_in_notice"
        return MoleculeMatch(
            product_molecule=product_molecule,
            molecule_detected=True,
            molecule_variant=variant,
            detection_method=method,
            atc_code=atc_code,
            matched_text=variant,
        )

    assert atc_hit is not None
    product_molecule, atc_code = atc_hit
    method = "atc_in_document" if context == "document" else "atc_in_notice"
    return MoleculeMatch(
        product_molecule=product_molecule,
        molecule_detected=False,
        detection_method=method,
        atc_code=atc_code,
        matched_text=atc_code,
    )


def match_text(
    text: str,
    config: dict[str, MoleculeConfig] | None = None,
    *,
    context: str = "document",
) -> MoleculeMatch | None:
    """Match molecule names and ATC codes with independent evidence evaluation."""
    return match_evidence(text, config, context=context)


def match_pack(pack: PackRecord, *, context: str = "document") -> MoleculeMatch | None:
    """Match a pack row using product fields and parsed active substance."""
    substance = str(pack.provenance.raw_values.get("active_substance", "") or "")
    text = f"{pack.product_name} {substance} {pack.atc_code}".strip()
    match = match_evidence(text, context=context)
    if match and pack.atc_code:
        match.atc_code = pack.atc_code
    return match
