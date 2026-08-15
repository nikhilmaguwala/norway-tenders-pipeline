from __future__ import annotations

import re

from norway_tenders.models import MoleculeMatch, NoticeRecord

PHARMA_BUYERS = {"sykehusinnkjøp hf", "sykehusinnkjop hf"}
EXAMPLE_CONTEXT = re.compile(
    r"examples?\s+of\s+medicines|relevant\s+for\s+analys|calibration|lc-ms|laboratory\s+equipment",
    re.I,
)


def is_pharmaceutical_evidence(notice: NoticeRecord, match: MoleculeMatch) -> bool:
    """Reject broad CPV hits where molecule is only an illustrative example."""
    buyer = notice.buyer.casefold()
    if buyer in PHARMA_BUYERS:
        return True

    title = notice.title.casefold()
    ref = notice.tender_ref.casefold()
    variant = (match.molecule_variant or "").casefold()

    if variant and variant in title:
        return True
    if re.search(rf"\blis\s*\d+", title):
        return True
    if re.search(r"\d{4}[a-z]{1,2}-\d", title):  # e.g. 2507gj-1 anagrelid
        return True

    description = notice.description.casefold()
    if EXAMPLE_CONTEXT.search(description) and variant in description:
        if variant not in title and variant not in ref:
            return False

    # Default: require molecule in title for non-Sykehusinnkjøp buyers
    if variant and variant in title:
        return True
    return False
