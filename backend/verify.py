"""Quote verification: does this exact passage exist in the cited document?

This is the guarantee behind "0 invented facts" and "100% traceability":
anything the model says must come with a quote that we can find in the
source; otherwise it never reaches the screen.

Match levels (strictest first):
  exact       raw substring
  normalized  same after whitespace/quote/dash/soft-hyphen normalisation
  caseless    same ignoring upper/lower case
  wrong_doc   quote exists, but in a different document (auto-corrected)
  not_found   rejected
An ellipsis in the quote ("A ... B") is allowed: each part must be found, in order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

from .corpus import Document, Patient
from .segment import passage_at
from .text_norm import normalize

MIN_QUOTE_CHARS = 5     # "BMI: 29" must be citable; shorter quotes prove nothing
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")


@dataclass
class Span:
    start: int
    end: int
    text: str


@dataclass
class VerifyResult:
    ok: bool
    status: str                      # exact | normalized | caseless | wrong_doc | not_found | too_short | unknown_doc
    doc_id: str | None               # the doc where it was actually found (may differ from cited)
    cited_doc_id: str | None
    spans: list[Span] = field(default_factory=list)
    passage_ids: list[str] = field(default_factory=list)
    n_occurrences: int = 0
    quote: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.spans:
            d["start"], d["end"] = self.spans[0].start, self.spans[-1].end
        return d


def _find_in_doc(doc: Document, part: str, from_raw: int = 0) -> tuple[str, int, int, int] | None:
    """Find one quote fragment. Returns (level, raw_start, raw_end, n_occurrences)."""
    if not part:
        return None
    i = _find_bounded(doc.text, part, from_raw)
    if i >= 0:
        return "exact", i, i + len(part), doc.text.count(part)
    for level, cf in (("normalized", False), ("caseless", True)):
        ntext, imap = doc.norm(cf)
        q = normalize(part, cf)
        if not q:
            continue
        # translate from_raw into normalised coordinates
        nstart = next((k for k, r in enumerate(imap) if r >= from_raw), len(imap))
        j = _find_bounded(ntext, q, nstart)
        if j >= 0:
            return level, imap[j], imap[j + len(q) - 1] + 1, ntext.count(q)
    return None


def _number_boundary_ok(text: str, s: int, e: int) -> bool:
    """A quote must not cut a number in half: 'BMI: 29' must not match inside 'BMI: 29.4',
    '100 mg' must not match inside '1100 mg'."""
    q = text[s:e]
    if q and q[-1].isdigit() and e < len(text):
        nxt = text[e]
        if nxt.isdigit() or (nxt in ".," and e + 1 < len(text) and text[e + 1].isdigit()):
            return False
    if q and q[0].isdigit() and s > 0:
        prv = text[s - 1]
        if prv.isdigit() or (prv in ".," and s > 1 and text[s - 2].isdigit()):
            return False
    return True


def _find_bounded(text: str, q: str, start: int = 0) -> int:
    i = text.find(q, start)
    while i >= 0 and not _number_boundary_ok(text, i, i + len(q)):
        i = text.find(q, i + 1)
    return i


_LEVEL_RANK = {"exact": 0, "normalized": 1, "caseless": 2}


def _verify_in_doc(doc: Document, quote: str):
    parts = [p for p in _ELLIPSIS.split(quote.strip().strip('"„“')) if p.strip()]
    if not parts:
        return None
    spans, worst, pos, occ = [], "exact", 0, 0
    for k, part in enumerate(parts):
        hit = _find_in_doc(doc, part, pos)
        if hit is None:
            return None
        level, s, e, n = hit
        if _LEVEL_RANK[level] > _LEVEL_RANK[worst]:
            worst = level
        spans.append(Span(s, e, doc.text[s:e]))
        occ = n if k == 0 else occ
        pos = e
    return worst, spans, occ


def resolve_doc(patient: Patient, doc_id: str | None) -> Document | None:
    """Tolerate 'doc.txt' vs 'doc' and case differences in model output."""
    if not doc_id:
        return None
    if doc_id in patient.documents:
        return patient.documents[doc_id]
    alt = {k.lower(): k for k in patient.documents}
    key = alt.get(doc_id.lower()) or alt.get(doc_id.lower().rsplit(".", 1)[0])
    return patient.documents[key] if key else None


def verify_quote(patient: Patient, doc_id: str | None, quote: str) -> VerifyResult:
    quote = (quote or "").strip()
    base = dict(cited_doc_id=doc_id, quote=quote)
    cited = resolve_doc(patient, doc_id)
    if len(normalize(quote)) < MIN_QUOTE_CHARS:
        # a short quote is accepted only when it is a WHOLE line of the cited document
        # (one-word diagnoses such as "SLE" or "HLP"); never searched in other documents
        if cited:
            nq = normalize(quote)
            for p in cited.passages:
                if p.kind != "heading" and normalize(p.text) == nq:
                    return VerifyResult(True, "exact", cited.doc_id, spans=[Span(p.start, p.end, p.text)],
                                        passage_ids=[p.id], n_occurrences=1, **base)
        return VerifyResult(False, "too_short", None, **base)

    order = ([cited] if cited else []) + [d for d in patient.documents.values() if d is not cited]

    for doc in order:
        res = _verify_in_doc(doc, quote)
        if res:
            level, spans, occ = res
            status = level if (doc is cited or not doc_id) else "wrong_doc"
            pids: list[str] = []
            for sp in spans:
                pids += [p for p in passage_at(doc.passages, sp.start, sp.end) if p not in pids]
            return VerifyResult(True, status, doc.doc_id, spans=spans, passage_ids=pids,
                                n_occurrences=occ, **base)
    return VerifyResult(False, "not_found" if order else "unknown_doc", None, **base)
