"""Map Stripe address fields to a Canadian province or territory."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from canada_sales_tax import PROVINCES

PROVINCES_SET = frozenset(PROVINCES)
OUTSIDE = "OUTSIDE-CA"
UNKNOWN = "UNKNOWN"

_CANADA = {"ca", "can", "canada"}

_EXTRA = {
    "pei": "PE", "p e i": "PE", "newfoundland": "NL", "labrador": "NL", "nf": "NL",
    "yukon territory": "YT", "nwt": "NT", "n w t": "NT", "ont": "ON", "que": "QC",
    "alta": "AB", "man": "MB", "sask": "SK", "n b": "NB", "n s": "NS", "b c": "BC",
    "new brunswick canada": "NB",
}


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for code, (en, fr) in PROVINCES.items():
        for name in (code, en, fr):
            lookup[_norm(name)] = code
    lookup.update(_EXTRA)
    return lookup


_LOOKUP = _build_lookup()


def province_from_text(text: str | None) -> str | None:
    """'NB', 'nb', 'New Brunswick', 'Nouveau-Brunswick', 'CA-ON', 'Quebec' -> code; else None."""
    key = _norm(text or "")
    if not key:
        return None
    if key.startswith("ca ") and key[3:] in _LOOKUP:  # ISO 3166-2 style: CA-ON
        key = key[3:]
    return _LOOKUP.get(key)


def is_canada(country: str | None) -> bool | None:
    """True for Canada, False for another country, None when the country is blank."""
    key = _norm(country or "")
    if not key:
        return None
    return key in _CANADA


@dataclass
class Location:
    province: str | None = None      # a code from PROVINCES
    outside_canada: bool = False
    country: str = ""                # the non-Canadian country seen
    unrecognized: str = ""           # state text that matched no province
    source: str = ""                 # which address group decided


def resolve_location(groups: list[tuple[str, str | None, str | None]]) -> Location:
    """Decide from (label, state, country) groups in order of preference.

    The first group that has any data decides whether the sale is outside Canada.
    Otherwise the first group whose state is a Canadian province decides the province.
    """
    loc = Location()
    seen_any = False
    for label, state, country in groups:
        state = (state or "").strip()
        country = (country or "").strip()
        if not state and not country:
            continue
        canada = is_canada(country)
        if not seen_any and canada is False:
            loc.outside_canada = True
            loc.country = country
            loc.source = label
            return loc
        seen_any = True
        if canada is False:
            continue
        code = province_from_text(state)
        if code:
            loc.province = code
            loc.source = label
            return loc
        if state and not loc.unrecognized:
            loc.unrecognized = state
    return loc
