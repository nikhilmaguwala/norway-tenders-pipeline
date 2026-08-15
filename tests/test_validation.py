from norway_tenders.models import MoleculeMatch, OutputRow
from norway_tenders.normalise.lifecycle import validate_output


def test_molecule_detected_requires_name_method() -> None:
    try:
        MoleculeMatch(
            product_molecule="Lenalidomide",
            molecule_detected=True,
            detection_method="atc_in_document",
        )
        assert False, "Should raise"
    except ValueError:
        pass


def test_row_key_with_and_without_item() -> None:
    row = OutputRow(
        tender_ref="LIS2234",
        product_molecule="Lenalidomide",
        item_number="123",
        notice_id="335380-2021",
    )
    assert row.row_key() == "LIS2234|Lenalidomide|123"

    row2 = OutputRow(
        tender_ref="LIS2234",
        product_molecule="Lenalidomide",
        notice_id="335380-2021",
    )
    assert row2.row_key() == "LIS2234|Lenalidomide|335380-2021"


def test_validate_unique_keys() -> None:
    rows = [
        OutputRow(
            notice_id="1",
            tender_ref="LIS1",
            product_molecule="Lenalidomide",
            molecule_detected=True,
            detection_method="name_in_notice",
            publication_date="2021-07-02",
            source_document="a.xml",
            source_url="https://example.com",
        ),
        OutputRow(
            notice_id="1",
            tender_ref="LIS1",
            product_molecule="Lenalidomide",
            molecule_detected=True,
            detection_method="name_in_notice",
            publication_date="2021-07-02",
            source_document="a.xml",
            source_url="https://example.com",
        ),
    ]
    result = validate_output(rows)
    assert any("duplicate" in e for e in result["errors"])
