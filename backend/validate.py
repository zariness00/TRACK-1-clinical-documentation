"""Turn model candidates into verified, renderable records — or reject them.

Guards (cheap, deterministic, run on every response):
  quote       every source quote must be found in the documents (verify.py)
  numbers     every number in the displayed value must appear in a quote
              (catches wrong doses/dates/values); skipped for derived values
  laterality  left/right in the value must match links/rechts in the quotes
A fact with no surviving source is dropped. A contradiction needs >= 2 verified
claims at different places in the documents.
"""
from __future__ import annotations

import re

from .corpus import Patient
from .models import (CandidateOutput, CandidateSource, Claim, Contradiction, Fact,
                     Missing, Rejected, Source)
from .verify import verify_quote

_INT = re.compile(r"\d+")
_LEFT = re.compile(r"\b(left|links|linke[nrsm]?|li\.?|sinistra|sin\.)\b", re.I)
_RIGHT = re.compile(r"\b(right|rechts|rechte[nrsm]?|re\.?|dextra|dex\.)\b", re.I)
_BOTH = re.compile(r"\b(bilateral|beidseits|bds\.?|both)\b", re.I)


def _ints(s: str) -> set[str]:
    return {m.lstrip("0") or "0" for m in _INT.findall(s)}


def numbers_ok(value: str, quotes: list[str]) -> bool:
    need = _ints(value)
    if not need:
        return True
    have = set().union(*(_ints(q) for q in quotes)) if quotes else set()
    return need <= have


def laterality_ok(value: str, quotes: list[str]) -> bool:
    q = " ".join(quotes)
    if _BOTH.search(value):
        return bool(_BOTH.search(q) or (_LEFT.search(q) and _RIGHT.search(q)))
    vl, vr = bool(_LEFT.search(value)), bool(_RIGHT.search(value))
    if vl and not vr:
        return bool(_LEFT.search(q) or _BOTH.search(q))
    if vr and not vl:
        return bool(_RIGHT.search(q) or _BOTH.search(q))
    return True


def to_source(patient: Patient, cs: CandidateSource) -> tuple[Source | None, str]:
    doc_id = cs.doc_id
    if not doc_id and cs.passage_id and cs.passage_id in patient.passage_index:
        doc_id = patient.passage_index[cs.passage_id].doc_id
    r = verify_quote(patient, doc_id, cs.quote)
    if not r.ok:
        return None, r.status
    doc = patient.documents[r.doc_id]
    return Source(
        doc_id=doc.doc_id, doc_title=doc.title, doc_date=doc.date,
        quote=" … ".join(s.text for s in r.spans),
        start=r.spans[0].start, end=r.spans[-1].end,
        spans=[(s.start, s.end) for s in r.spans],
        passage_ids=r.passage_ids, match=r.status,
    ), r.status


def validate(patient: Patient, cand: CandidateOutput) -> tuple[list[Fact], list[Contradiction], list[Missing], list[Rejected]]:
    facts: list[Fact] = []
    contras: list[Contradiction] = []
    rejected: list[Rejected] = []

    for i, cf in enumerate(cand.facts):
        srcs: list[Source] = []
        for cs in cf.sources:
            s, status = to_source(patient, cs)
            if s:
                srcs.append(s)
            else:
                rejected.append(Rejected(kind="source", item=f"{cf.label}: {cs.quote[:80]}", reason=status))
        if not srcs:
            rejected.append(Rejected(kind="fact", item=f"{cf.label}: {cf.value}", reason="no verified source"))
            continue
        quotes = [s.quote for s in srcs]
        checks = ["quote"]
        if not cf.derived:
            if not numbers_ok(cf.value, quotes):
                rejected.append(Rejected(kind="fact", item=f"{cf.label}: {cf.value}", reason="number in value not in quote"))
                continue
            checks.append("numbers")
        if not laterality_ok(cf.value, quotes):
            rejected.append(Rejected(kind="fact", item=f"{cf.label}: {cf.value}", reason="laterality mismatch"))
            continue
        checks.append("laterality")
        facts.append(Fact(id=f"f{i}", category=cf.category, label=cf.label, value=cf.value,
                          date=cf.date, derived=cf.derived, sources=srcs, checks=checks))

    for i, cc in enumerate(cand.contradictions):
        claims: list[Claim] = []
        seen: set[tuple[str, int]] = set()
        for cl in cc.claims:
            s, status = to_source(patient, cl.source)
            if s is None:
                rejected.append(Rejected(kind="source", item=f"{cc.summary}: {cl.source.quote[:80]}", reason=status))
                continue
            key = (s.doc_id, s.start)
            if key in seen:
                continue
            seen.add(key)
            claims.append(Claim(statement=cl.statement, source=s))
        if len(claims) < 2:
            rejected.append(Rejected(kind="contradiction", item=cc.summary, reason="fewer than 2 verified passages"))
            continue
        contras.append(Contradiction(id=f"c{i}", category=cc.category, summary=cc.summary, claims=claims))

    missing = [Missing(**m.model_dump()) for m in cand.not_documented]
    # a "not documented" item contradicted by a verified fact in the same category is dropped
    covered = {f.category for f in facts}
    kept = []
    for m in missing:
        if m.category in covered and any(m.item.lower() in f.label.lower() for f in facts if f.category == m.category):
            rejected.append(Rejected(kind="missing", item=m.item, reason="a verified fact covers it"))
        else:
            kept.append(m)
    return facts, contras, kept, rejected
