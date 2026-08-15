from norway_tenders.matching.matcher import (
    load_molecule_config,
    match_evidence,
    match_pack,
    match_text,
    parse_norwegian_number,
    parse_pack_size,
)
from norway_tenders.models import PackRecord, Provenance


def test_parse_norwegian_number() -> None:
    assert parse_norwegian_number("1 234,56") == 1234.56
    assert parse_norwegian_number("325240414,61") == 325240414.61
    assert parse_norwegian_number("") is None
    assert parse_norwegian_number("-") is None
    assert parse_norwegian_number(7188) == 7188.0


def test_parse_pack_size_enpac() -> None:
    assert parse_pack_size("21 ENPAC") == 21
    assert parse_pack_size(28) == 28


def test_molecule_config_loads_five_molecules() -> None:
    config = load_molecule_config()
    assert set(config) == {
        "Axitinib",
        "Everolimus",
        "Lenalidomide",
        "Anagrelide",
        "Paliperidone",
    }
    assert config["Axitinib"].discovery_brands == ("inlyta",)


def test_name_match_preserves_variant() -> None:
    match = match_text("Levering av Lenalidomid til RHF", context="notice")
    assert match is not None
    assert match.product_molecule == "Lenalidomide"
    assert match.molecule_detected is True
    assert match.detection_method == "name_in_notice"
    assert match.molecule_variant == "Lenalidomid"


def test_atc_match_not_molecule_detected() -> None:
    match = match_text("ATC L04AX04 applies", context="document")
    assert match is not None
    assert match.molecule_detected is False
    assert match.detection_method == "atc_in_document"
    assert match.atc_code == "L04AX04"


def test_unicode_case_insensitive() -> None:
    match = match_text("Paliperidon depot", context="notice")
    assert match is not None
    assert match.product_molecule == "Paliperidone"


def test_anagrelide_historical_atc() -> None:
    config = load_molecule_config()
    assert "B01AC14" in config["Anagrelide"].atc_codes


def test_name_and_atc_present() -> None:
    match = match_evidence(
        "VIRKESTOFF Lenalidomid ATC L04AX04 Revlimid",
        context="document",
    )
    assert match is not None
    assert match.product_molecule == "Lenalidomide"
    assert match.molecule_detected is True
    assert match.molecule_variant == "Lenalidomid"
    assert match.detection_method == "name_in_document"
    assert match.atc_code == "L04AX04"


def test_name_present_without_atc() -> None:
    match = match_evidence("Virkestoff: Lenalidomid", context="document")
    assert match is not None
    assert match.product_molecule == "Lenalidomide"
    assert match.molecule_detected is True
    assert match.detection_method == "name_in_document"
    assert match.molecule_variant == "Lenalidomid"
    assert match.atc_code == ""


def test_atc_present_without_name() -> None:
    match = match_evidence("Preparat med ATC L04AX04", context="document")
    assert match is not None
    assert match.product_molecule == "Lenalidomide"
    assert match.molecule_detected is False
    assert match.detection_method == "atc_in_document"
    assert match.atc_code == "L04AX04"
    assert match.molecule_variant == ""


def test_neither_name_nor_atc_present() -> None:
    assert match_evidence("Generisk legemiddel uten identifikator", context="document") is None


def test_norwegian_lenalidomid_normalises_to_lenalidomide() -> None:
    match = match_evidence("Lenalidomid", context="document")
    assert match is not None
    assert match.product_molecule == "Lenalidomide"
    assert match.molecule_detected is True


def test_source_spelling_preserved_in_molecule_variant() -> None:
    match = match_evidence("LENALIDOMID i tabell", context="document")
    assert match is not None
    assert match.molecule_variant == "LENALIDOMID"


def test_match_pack_lis2234_semantics() -> None:
    pack = PackRecord(
        product_name="Revlimid",
        atc_code="L04AX04",
        provenance=Provenance(raw_values={"active_substance": "Lenalidomid"}),
    )
    match = match_pack(pack, context="document")
    assert match is not None
    assert match.product_molecule == "Lenalidomide"
    assert match.molecule_detected is True
    assert match.molecule_variant == "Lenalidomid"
    assert match.detection_method == "name_in_document"
    assert match.atc_code == "L04AX04"
