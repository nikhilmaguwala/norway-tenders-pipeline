from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VolumeSemantics:
  volume_period_label: str
  volume_period_start: str
  volume_period_end: str
  volume_is_twelve_months: bool
  volume_interpretation_warning: str
  populate_packs_sold_last_12m: bool
  evidence: str


def volume_semantics_for_source(source_document: str, volume_label: str = "", packs_year: int | None = None) -> VolumeSemantics:
  """Derive documented volume period semantics from tender source context."""
  name = (source_document or "").casefold()
  label = volume_label or ""

  if "2601c" in name and "prisskjema" in name:
    return VolumeSemantics(
      volume_period_label="Solgte pakninger siste 12 mnd (Pakningssalg)",
      volume_period_start="",
      volume_period_end="",
      volume_is_twelve_months=True,
      volume_interpretation_warning=(
        "Column labelled 'Pakningssalg' with sub-header 'Solgte pakninger siste 12 mnd'; "
        "Veiledning states volumes from Sykehusapotekenes legemiddelstatistikk without calendar dates."
      ),
      populate_packs_sold_last_12m=True,
      evidence=(
        "2601c_Bilag_2_Prisskjema.xlsx: Veiledning row 9 "
        "'Antall pakninger er hentet fra Sykehusapotekenes legemiddelstatistikk'; "
        "Prisskjema row 3 'Solgte pakninger siste 12 mnd'; header row 4 'Pakningssalg'."
      ),
    )

  if packs_year is not None:
    return VolumeSemantics(
      volume_period_label=f"PAKNINGER {packs_year}",
      volume_period_start=f"{packs_year}-01-01",
      volume_period_end=f"{packs_year}-12-31",
      volume_is_twelve_months=True,
      volume_interpretation_warning=(
        f"Calendar-year proxy PAKNINGER {packs_year} used as documented 12-month historical volume."
      ),
      populate_packs_sold_last_12m=True,
      evidence=f"Prisskjema header PAKNINGER {packs_year} (full calendar year).",
    )

  if label.upper().startswith("PAKNINGER "):
    year_text = label.split()[-1]
    if year_text.isdigit():
      year = int(year_text)
      return volume_semantics_for_source(source_document, packs_year=year)

  return VolumeSemantics(
    volume_period_label=label,
    volume_period_start="",
    volume_period_end="",
    volume_is_twelve_months=False,
    volume_interpretation_warning="Volume period not documented; packsSoldLast12m left blank.",
    populate_packs_sold_last_12m=False,
    evidence="No explicit 12-month or calendar-year volume label in source.",
  )
