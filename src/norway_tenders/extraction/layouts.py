from __future__ import annotations

from norway_tenders.parsers.lis_excel import SheetLayout

# Verified layouts from Phase 5A parser_layout_report.csv
PRICE_SCHEDULE_LAYOUTS: dict[str, SheetLayout] = {
    "Axitinib__LIS_2207_Oncology/LIS 2207 - Vedlegg 03 Prisskjema v 2.xlsx": SheetLayout(
        sheet_name="Prisskjema", header_row=3, data_start_row=4
    ),
    "Lenalidomide__LIS_2234/LIS 2234 Vedlegg 03 Prisskjema.xlsx": SheetLayout(),
    "Everolimus__2632a/2632a Bilag 2 Prisskjema.xlsx": SheetLayout(
        sheet_name="Prisskjema", header_row=4, data_start_row=5
    ),
    "Anagrelide__2507gj-1/2507gj-1 Bilag 2 Prisskjema.xlsx": SheetLayout(
        sheet_name="Prisskjema", header_row=4, data_start_row=5
    ),
    "Paliperidone__LIS_2301d/2301d Vedlegg 03 Prisskjema legem versj1.xlsx": SheetLayout(
        sheet_name="Prisskjema", header_row=3, data_start_row=4
    ),
    "Paliperidone__2601c/2601c_Bilag_2_Prisskjema.xlsx": SheetLayout(
        sheet_name="Prisskjema", header_row=4, data_start_row=5
    ),
}


def layout_for_local_file(local_file: str) -> SheetLayout:
    return PRICE_SCHEDULE_LAYOUTS.get(local_file, SheetLayout())
