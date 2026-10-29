"""Normalisation with an offset map back to the raw text.

Every comparison (quote checking, search) runs on normalised text, but every
position we hand out refers to the RAW document text, so the UI can highlight
exactly what is on the page.
"""
from __future__ import annotations

import re
import unicodedata

_CHAR_MAP = {
    "„": '"', "“": '"', "”": '"', "«": '"', "»": '"',
    "‚": "'", "‘": "'", "’": "'", "´": "'", "`": "'",
    "–": "-", "—": "-", "‑": "-", "‐": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
    "…": "...",
}
_DROP = {"­", "​", "﻿"}


def normalize_with_map(raw: str, casefold: bool = True) -> tuple[str, list[int]]:
    """Return (normalised_text, idx_map) where idx_map[i] = raw index of normalised char i.

    - unicode NFKC, typographic quotes/dashes unified, soft hyphens dropped
    - any whitespace run -> one space
    - hyphenated line breaks joined ("Thrombo-\\nzyten" -> "Thrombozyten")
    - optional lower-casing
    """
    out: list[str] = []
    idx: list[int] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        # hyphen + line break + lowercase letter  => word was split across lines
        if ch == "-" and i + 1 < n and raw[i + 1] in "\r\n":
            j = i + 1
            while j < n and raw[j].isspace():
                j += 1
            if j < n and raw[j].islower():
                i = j
                continue
        if ch in _DROP:
            i += 1
            continue
        if ch.isspace() or ch in (" ", " ", " "):
            if out and out[-1] != " ":
                out.append(" ")
                idx.append(i)
            i += 1
            continue
        rep = _CHAR_MAP.get(ch)
        if rep is None:
            rep = unicodedata.normalize("NFKC", ch)
        if casefold:
            rep = rep.lower()
        for c in rep:
            out.append(c)
            idx.append(i)
        i += 1
    # strip trailing space
    while out and out[-1] == " ":
        out.pop(); idx.pop()
    return "".join(out), idx


def normalize(s: str, casefold: bool = True) -> str:
    return normalize_with_map(s, casefold)[0].strip()


_TOKEN = re.compile(r"[a-zäöüß0-9]+", re.I)
_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def tokens(s: str) -> list[str]:
    """Search tokens: lower-case, umlauts folded (so 'Groesse' matches 'Größe')."""
    return [t.translate(_UMLAUT) for t in _TOKEN.findall(normalize(s))]
