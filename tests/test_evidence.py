from norway_tenders.matching.evidence import is_pharmaceutical_evidence
from norway_tenders.models import MoleculeMatch, NoticeRecord


def test_rejects_lab_equipment_everolimus_example() -> None:
    notice = NoticeRecord(
        notice_id="x",
        title="LC-MS/MS analysis platform",
        buyer="Oslo Universitetssykehus HF",
        description="Examples of medicines: everolimus, tacrolimus",
    )
    match = MoleculeMatch(
        product_molecule="Everolimus",
        molecule_detected=True,
        molecule_variant="everolimus",
        detection_method="name_in_notice",
    )
    assert is_pharmaceutical_evidence(notice, match) is False


def test_accepts_sykehusinnkjop_tender() -> None:
    notice = NoticeRecord(
        notice_id="x",
        title="2507gj-1 anagrelid",
        buyer="SYKEHUSINNKJØP HF",
        description="Framework agreement for anagrelid",
    )
    match = MoleculeMatch(
        product_molecule="Anagrelide",
        molecule_detected=True,
        molecule_variant="anagrelid",
        detection_method="name_in_notice",
    )
    assert is_pharmaceutical_evidence(notice, match) is True
