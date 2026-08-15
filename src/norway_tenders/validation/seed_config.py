from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedFolderMeta:
    target_molecule: str
    folder: str
    procurement_family: str
    notice_id: str
    tender_ref: str
    title: str
    notice_url: str
    landing_page: str
    access_status: str
    linkage_needs_review: bool = False
    linkage_note: str = ""


SEED_FOLDER_META: dict[str, SeedFolderMeta] = {
    "Axitinib__LIS_2207_Oncology": SeedFolderMeta(
        target_molecule="Axitinib",
        folder="Axitinib__LIS_2207_Oncology",
        procurement_family="LIS 2207",
        notice_id="196990-2022",
        tender_ref="2021/1727",
        title="LIS 2207 Onkologi",
        notice_url="https://ted.europa.eu/en/notice/-/detail/196990-2022",
        landing_page="https://www.mercell.com/nb-no/anbud/176511702/lis-2207-onkologi-anbud.aspx",
        access_status="downloaded_manually",
    ),
    "Lenalidomide__LIS_2234": SeedFolderMeta(
        target_molecule="Lenalidomide",
        folder="Lenalidomide__LIS_2234",
        procurement_family="LIS 2234",
        notice_id="300984-2021",
        tender_ref="LIS 2234",
        title="LIS 2234",
        notice_url="https://ted.europa.eu/en/notice/-/detail/300984-2021",
        landing_page="",
        access_status="supplied_seed",
    ),
    "Everolimus__2632a": SeedFolderMeta(
        target_molecule="Everolimus",
        folder="Everolimus__2632a",
        procurement_family="2632a",
        notice_id="404973-2025",
        tender_ref="2025/50837",
        title="2632a Everolimus og mykofenolsyre (enterotablett)",
        notice_url="https://ted.europa.eu/en/notice/-/detail/404973-2025",
        landing_page="https://www.mercell.com/nb-no/anbud/258634247/2632a-everolimus-og-mykofenolsyre-enterotablett-anbud.aspx",
        access_status="downloaded_manually",
    ),
    "Anagrelide__2507gj-1": SeedFolderMeta(
        target_molecule="Anagrelide",
        folder="Anagrelide__2507gj-1",
        procurement_family="2507gj-1",
        notice_id="244859-2024",
        tender_ref="2507gj-1",
        title="2507gj-1 anagrelid",
        notice_url="https://ted.europa.eu/en/notice/-/detail/244859-2024",
        landing_page="https://www.mercell.com/nb-no/anbud/227565253/2507gj-1-anagrelid-anbud.aspx",
        access_status="downloaded_manually",
        linkage_needs_review=False,
        linkage_note="TED internal ref 2024/206; procurement family 2507gj-1 confirmed in notice title",
    ),
    "Paliperidone__LIS_2301d": SeedFolderMeta(
        target_molecule="Paliperidone",
        folder="Paliperidone__LIS_2301d",
        procurement_family="LIS 2301d",
        notice_id="682047-2022",
        tender_ref="2022/227",
        title="LIS 2301d intensjonskunngjøring",
        notice_url="https://ted.europa.eu/en/notice/-/detail/682047-2022",
        landing_page="https://www.mercell.com/nb-no/anbud/191826142/intensjonskunngjoering--tilleggsavtaler-til-lis-2301d-ureg-atc-koder-j05af01-n05ax13-n05cd08-p01cx04-s01ax18-s01ee09-og-v03ab13-anbud.aspx",
        access_status="downloaded_manually",
    ),
    "Paliperidone__2601c": SeedFolderMeta(
        target_molecule="Paliperidone",
        folder="Paliperidone__2601c",
        procurement_family="2601c",
        notice_id="434619-2026",
        tender_ref="2601c",
        title="2601c paliperidone",
        notice_url="https://ted.europa.eu/en/notice/-/detail/434619-2026",
        landing_page="https://sykehusinnkjop.ivalua.app/page.aspx/nb/bpm/process_manage_extranet/1281",
        access_status="downloaded_manually",
        linkage_needs_review=False,
        linkage_note="Distinct 2026 paliperidone competition; not merged with LIS 2301d",
    ),
}

HUMAN_READABLE_SEED_FOLDERS: tuple[str, ...] = tuple(SEED_FOLDER_META.keys())

PALIPERIDONE_NOTICE_TOTAL_NOK = 14_671_946
