from pathlib import Path

import pytest

from norway_tenders.parsers.lis_excel import (
    parse_kravspec_omfang,
    parse_kravspec_product_requirements,
    parse_lis_prisskjema,
)

SEEDS = Path(__file__).resolve().parents[1] / "data" / "seeds"
FIXTURES = Path(__file__).parent / "fixtures"
PRISSKJEMA = SEEDS / "Lenalidomide__LIS_2234" / "LIS 2234 Vedlegg 03 Prisskjema.xlsx"
KRAVSPEC = SEEDS / "Lenalidomide__LIS_2234" / "LIS 2234 Vedlegg 02 Kravspesifikasjon.xlsx"


def _resolve(path: Path) -> Path:
    if path.exists() and path.read_bytes()[:2] == b"PK":
        return path
    alt = FIXTURES / path.name
    if alt.exists() and alt.read_bytes()[:2] == b"PK":
        return alt
    pytest.skip(f"Golden fixture not available: {path.name}")


@pytest.mark.golden
def test_lenalidomid_seven_pack_rows() -> None:
    path = _resolve(PRISSKJEMA)
    packs = parse_lis_prisskjema(path)
    lenalidomid = [p for p in packs if p.atc_code == "L04AX04"]
    assert len(lenalidomid) == 7


@pytest.mark.golden
def test_lenalidomid_atc_and_volume() -> None:
    path = _resolve(PRISSKJEMA)
    packs = parse_lis_prisskjema(path)
    lenalidomid = [p for p in packs if p.atc_code == "L04AX04"]
    assert all(p.atc_code == "L04AX04" for p in lenalidomid)
    total = sum(p.packs_sold_last_12m or 0 for p in lenalidomid)
    assert total == 7188


@pytest.mark.golden
def test_item_numbers_preserved_as_strings() -> None:
    path = _resolve(PRISSKJEMA)
    packs = parse_lis_prisskjema(path)
    for pack in packs:
        if pack.item_number:
            assert isinstance(pack.item_number, str)


@pytest.mark.golden
def test_pack_size_enpac_rule() -> None:
    path = _resolve(PRISSKJEMA)
    packs = parse_lis_prisskjema(path)
    assert all(p.pack_size == 21 for p in packs)
    assert all(isinstance(p.pack_size, int) for p in packs)


@pytest.mark.golden
def test_tilbudt_gip_blank() -> None:
    path = _resolve(PRISSKJEMA)
    packs = parse_lis_prisskjema(path)
    assert all(p.offered_gip is None for p in packs)


@pytest.mark.golden
def test_kravspec_historical_turnover() -> None:
    path = _resolve(KRAVSPEC)
    evidence = parse_kravspec_omfang(path)
    assert evidence.get("historical_turnover_aip") == pytest.approx(325240414.61)
    assert evidence.get("atc_code") == "L04AX04"


@pytest.mark.golden
def test_kravspec_product_requirements_evidence() -> None:
    path = _resolve(KRAVSPEC)
    evidence = parse_kravspec_product_requirements(path)
    assert evidence.price_weighting_percent == 100.0
    assert evidence.min_discount_max_aip_percent == 5.0
    assert evidence.offered_price_basis == "GIP in NOK per item number"
    assert evidence.equal_price_per_mg_within_formulation is True
