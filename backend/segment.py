"""Split a German clinical document into citable passages with exact character
offsets into the raw text.

Tuned for the Track-1 letters: one fact per line ("Größe: 170 cm", one
diagnosis per line, one medication per row), with prose wrapped mid-sentence
across <BR> lines. Units are lines; wrapped prose lines are re-joined; a unit
with several sentences is split into sentences.

Design rule: every passage.text == raw[start:end]. Nothing is rewritten.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

# Abbreviations that end with "." but do not end a sentence (lower-case, without the final dot)
ABBREV = {
    "dr", "prof", "med", "dipl", "pd", "priv", "doz", "hr", "fr", "frau", "herr", "pat", "prim", "oa",
    "z.b", "ca", "bzw", "ggf", "evtl", "inkl", "exkl", "usw", "etc", "vgl", "s", "geb", "wg", "gem",
    "u.a", "d.h", "o.g", "o.ä", "s.o", "s.u", "z.n", "v.a", "vd.a", "a.e", "i.v", "s.c", "p.o", "i.m", "st.p",
    "re", "li", "sin", "dex", "bds", "nr", "tbl", "tabl", "kps", "trpf", "amp", "st", "stat", "amb", "lt",
    "diagn", "ther", "anamn", "vs", "max", "min", "mind", "tägl", "ggü", "ventr", "art", "diab", "mell",
    "kalk", "systol", "diastol", "re", "jan", "feb", "mär", "apr", "jun", "jul", "aug", "sep", "sept",
    "okt", "nov", "dez", "mo", "di", "mi", "do", "sa", "so", "abt", "tel", "str", "j", "jr", "mon",
    "zust", "allg", "neg", "pos", "path", "a", "aa", "v", "vv", "lk", "lnn", "ass",
}

_JOINERS = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "einem", "einen", "eines",
            "und", "oder", "mit", "von", "vom", "zu", "zur", "zum", "in", "im", "am", "an", "auf", "bei",
            "für", "über", "unter", "nach", "vor", "aus", "sowie", "welche", "welcher", "als", "durch",
            "bis", "seit", "ohne", "gegen", "linken", "rechten", "beiden", "keine", "kein", "eines"}
_SENT_END = re.compile(r"[.;!](?=\s+[\"„(\[]?[A-ZÄÖÜ0-9])")
_LIST_ITEM = re.compile(r"^\s*(?:[-•*·▪–]|\d{1,2}[.)]|[a-z][.)])\s+")
_MULTI_SPACE = re.compile(r"\S {3,}\S")
_SMALL_NUM = re.compile(r"(?<!\S)\d(?:/\d)?(?!\S)")   # schema digits "1 0 1/2 0"


@dataclass
class Passage:
    id: str          # "<doc_id>#p<n>"
    doc_id: str
    n: int
    kind: str        # sentence | line | field | table_row | list_item | heading
    start: int       # raw offsets, end exclusive
    end: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _line_kind(s: str) -> str:
    if "\t" in s or s.count("|") >= 2 or _MULTI_SPACE.search(s):
        return "table_row"
    if len(_SMALL_NUM.findall(s)) >= 3 and not re.search(r"[.;]$", s):
        return "table_row"            # "Thrombo ASS 100mg oral 1 0 0 0 ST"
    if _LIST_ITEM.match(s):
        return "list_item"
    if s.endswith(":") and len(s.split()) <= 8:
        return "heading"
    if re.match(r"^[\wÄÖÜäöüß./()\- ]{1,40}:\s+\S", s) and len(s) <= 80:
        return "field"                # "Größe: 170 cm", "Allergie: KM-Allergie ..."
    return "line"


def _continues(prev: str, nxt: str) -> bool:
    """Is `nxt` the wrapped continuation of `prev`'s sentence?"""
    if not prev or not nxt or re.search(r"[.:;!?]$", prev):
        return False
    if _line_kind(prev) in ("table_row", "heading") or \
            _line_kind(nxt) in ("table_row", "heading", "list_item", "field"):
        return False
    if len(prev) < 25:
        return False
    first = nxt[0]
    if first.islower() or first.isdigit() or first == "(":
        return True
    # German nouns are capitalised: "... in der rechten\nSchulter" — prev ends mid-phrase
    last = re.findall(r"[\wäöüß]+$", prev)
    return bool(last) and (last[0] in _JOINERS or last[0].islower() and prev.split()[-1] == last[0]
                           and len(prev) >= 60)


def _is_false_boundary(text: str, dot_pos: int) -> bool:
    """True if the '.' at dot_pos is an abbreviation / number dot, not a sentence end."""
    if text[dot_pos] != ".":
        return False
    j = dot_pos
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    word = text[j:dot_pos].strip("(\"„[").lower()
    if not word:
        return False
    if word in ABBREV or word.rstrip(".") in ABBREV:
        return True
    if len(word) == 1:                                   # initials, "Z. n."
        return True
    if re.fullmatch(r"\d{1,2}|\d{1,2}\.\d{1,2}", word):  # ordinal "03. April", date "12.03."
        return True
    if "." in word and len(word) <= 6 and not word[-1].isdigit():   # "z.B", "u.a", "St.p"
        return True
    return False


def _split_sentences(text: str, base: int) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _SENT_END.finditer(text):
        p = m.start()
        if _is_false_boundary(text, p):
            continue
        spans.append((start, p + 1))
        start = p + 1
    spans.append((start, len(text)))
    out = []
    for s, e in spans:
        seg = text[s:e]
        ls = len(seg) - len(seg.lstrip())
        rs = len(seg.rstrip())
        if rs > ls:
            out.append((base + s + ls, base + s + rs))
    return out


def _lines(raw: str) -> list[tuple[int, int]]:
    """(start, end) of each non-empty line, trimmed."""
    out, pos = [], 0
    for line in raw.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        s, e = pos, pos + len(body)
        while s < e and raw[s].isspace():
            s += 1
        while e > s and raw[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
        pos += len(line)
    return out


def segment(doc_id: str, raw: str) -> list[Passage]:
    passages: list[Passage] = []

    def add(kind: str, s: int, e: int):
        while s < e and raw[s].isspace():
            s += 1
        while e > s and raw[e - 1].isspace():
            e -= 1
        if e > s:
            n = len(passages)
            passages.append(Passage(f"{doc_id}#p{n}", doc_id, n, kind, s, e, raw[s:e]))

    # 1) group wrapped lines into units (a blank line between them is allowed:
    #    "Die Ausdehnung des Tumors\n\nwurde als 3cm beschrieben.")
    units: list[list[int]] = []
    for s, e in _lines(raw):
        if units:
            ps, pe = units[-1]
            if _continues(raw[ps:pe], raw[s:e]):
                units[-1][1] = e
                continue
        units.append([s, e])

    # 2) emit passages; multi-sentence units are split
    for s, e in units:
        text = raw[s:e]
        kind = _line_kind(text.split("\n", 1)[0].strip())
        if kind in ("table_row", "heading"):
            add(kind, s, e)
            continue
        spans = _split_sentences(text, s)
        if len(spans) == 1:
            add("sentence" if kind == "line" and len(text) > 60 else kind, s, e)
        else:
            for a, b in spans:
                add("sentence" if kind != "list_item" else "list_item", a, b)
    return passages


def passage_at(passages: list[Passage], start: int, end: int) -> list[str]:
    """IDs of passages overlapping a raw span."""
    return [p.id for p in passages if p.start < end and start < p.end]
