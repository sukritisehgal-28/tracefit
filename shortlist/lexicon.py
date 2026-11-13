"""The vocabulary the text reader recognises, loaded from configuration."""
from __future__ import annotations

import re

STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}
STATE_NAMES = {code: name for name, code in STATES.items()}
STATE_RE = "(?:" + "|".join(sorted((re.escape(n) for n in STATES), key=len, reverse=True)) + ")"
_BY_LOWER = {name.lower(): code for name, code in STATES.items()}


def state_code(value: str | None) -> str | None:
    """'Washington' or 'WA' -> 'WA'. Unknown values are returned unchanged."""
    if not value:
        return None
    value = value.strip()
    if value.upper() in STATE_NAMES:
        return value.upper()
    return _BY_LOWER.get(value.lower(), value)


def _compile(term: str) -> re.Pattern:
    if term.isupper() and len(term) <= 6:      # acronym: exact case, whole token
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])")
    body = re.escape(term).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]+")
    return re.compile(r"(?<![A-Za-z])" + body + r"(?![A-Za-z])", re.IGNORECASE)


_OTHER_ROLE = re.compile(r"\b(?:assistant|aide|technician|tech|student|extern|intern|scribe|trainee)\b", re.I)


class Lexicon:
    def __init__(self, cfg: dict):
        self.families: dict[str, list[tuple[str, re.Pattern]]] = {}
        for family, entries in cfg.get("lexicon", {}).items():
            self.families[family] = [(canon, _compile(term)) for canon, terms in entries.items() for term in terms]
        self.labels: dict[str, str] = dict(cfg.get("labels", {}))

    def spans(self, family: str, text: str) -> list[tuple[int, str]]:
        """(position, canonical key) for each key of this family mentioned in the text, first mention only."""
        first: dict[str, int] = {}
        if family == "profession" and len(text) <= 60 and _OTHER_ROLE.search(text):
            return []          # "Physician Assistant", "Pharmacy Technician (PT)": a different job that contains the word
        for canon, pattern in self.families.get(family, ()):
            m = pattern.search(text)
            if m and (canon not in first or m.start() < first[canon]):
                first[canon] = m.start()
        return sorted((pos, canon) for canon, pos in first.items())

    def find(self, family: str, text: str) -> list[str]:
        """Canonical keys of this family mentioned in the text, in order of first appearance."""
        return [canon for _, canon in self.spans(family, text)]

    def first(self, family: str, text: str) -> str | None:
        found = self.find(family, text)
        return found[0] if found else None

    def label(self, key: str | None) -> str:
        if key is None:
            return "—"
        if key in self.labels:
            return self.labels[key]
        if key.startswith("board:"):
            return key[6:].replace("_", " ") + " board certification"
        return key.replace("_", " ")
