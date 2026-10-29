"""Turn model candidates into verified, renderable records — or reject them.

Guards (cheap, deterministic, run on every response):
  quote       every source quote must be found in the documents (verify.py)
  numbers     every number in the displayed value must appear in a quote
              (catches wrong doses/dates/values); skipped for derived values
  laterality  left/right in the value must match links/rechts in the quotes
  negation    "no/negative" must match kein/nicht/negativ in the quote, and vice versa
  certainty   "Vd.a./Verdacht" (suspected) must not be shown as confirmed
"Not documented" claims are dropped if the passages do contain that information.
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


_ROMAN = re.compile(r"(?<![A-Za-z])(I{1,3}|IV)(?=°|\b)")
_ROMAN_VAL = {"I": "1", "II": "2", "III": "3", "IV": "4"}


def _ints(s: str, roman: bool = False) -> set[str]:
    out = {m.lstrip("0") or "0" for m in _INT.findall(s)}
    if roman:   # "Typ II", "Grad II", "GERD II°" in the source may be shown as 2
        out |= {_ROMAN_VAL[m] for m in _ROMAN.findall(s)}
    return out


def numbers_ok(value: str, quotes: list[str]) -> bool:
    need = _ints(value)
    if not need:
        return True
    have = set().union(*(_ints(q, roman=True) for q in quotes)) if quotes else set()
    return need <= have


_POS_EN = re.compile(r"\bpositive\b|\+", re.I)
_NEG_WORD_EN = re.compile(r"\bnegative\b", re.I)
_POS_DE = re.compile(r"positiv", re.I)
_NEG_WORD_DE = re.compile(r"negativ", re.I)


def polarity_ok(value: str, quotes: list[str]) -> bool:
    """'HER2 positive' must not come from 'Her2neu-Expression: negativ' (and vice versa)."""
    q = " ".join(quotes)
    if _POS_EN.search(value) and not _NEG_WORD_EN.search(value):
        return bool(_POS_DE.search(q)) or not _NEG_WORD_DE.search(q)
    if _NEG_WORD_EN.search(value) and not _POS_EN.search(value):
        return bool(_NEG_WORD_DE.search(q)) or not _POS_DE.search(q)
    return True


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


# ---------- negation / certainty ----------
_NEG_DE = re.compile(r"\b(kein|keine|keinen|keiner|keinem|keines|nicht|ohne|negativ|ausgeschlossen|kein Anhalt)\b", re.I)
_NEG_EN = re.compile(r"\b(no|not|none|without|negative|absent|excluded|ruled out|denies|nicht|kein\w*|ohne|negativ)\b", re.I)
_SUSPECT_DE = re.compile(r"(\bV\.?\s?a\.|\bVd\.?\s?a\.|Verdacht|suspekt|fraglich|\bDD\b|malignomsuspekt|Fragestellung|\?)", re.I)
_SUSPECT_EN = re.compile(r"\b(suspect\w*|suspicion|possible|possibly|probable|query|queried|question\w*|rule out|r/o|likely|unconfirmed|vd\.?a\.?)\b|\?", re.I)
_CLAUSE_SPLIT = re.compile(r"[,;:()]|\s-\s|\bsowie\b|\bund\b")


def negation_ok(value: str, quotes: list[str]) -> bool:
    """Reject facts that flip a negation.
    - value says "no/negative/without" but no quote is negated   -> reject
    - a quote is ONE short negated clause but the value is affirmative -> reject
      ("axillär keine vergrößerten Lymphknoten" shown as "Enlarged lymph nodes")
    Long multi-clause quotes are not judged in the second direction: their
    negation may belong to a different clause than the displayed fact."""
    v_neg = bool(_NEG_EN.search(value))
    q_negs = [bool(_NEG_DE.search(q)) for q in quotes]
    if v_neg and not any(q_negs) and not any(_NEG_EN.search(q) for q in quotes):
        return False
    if not v_neg:
        for q, neg in zip(quotes, q_negs):
            single_clause = len([c for c in _CLAUSE_SPLIT.split(q) if c.strip()]) == 1
            if neg and single_clause and len(q.split()) <= 14:
                return False
    return True


def certainty_ok(value: str, quotes: list[str]) -> bool:
    """A suspected finding ("Vd.a. Mamma-Ca") or a clinical question ("Fragestellung: Carotisstenose?")
    must not be shown as a confirmed finding,
    unless another cited quote states it without the suspicion marker."""
    suspected = [bool(_SUSPECT_DE.search(q)) for q in quotes]
    if suspected and all(suspected) and not _SUSPECT_EN.search(value):
        return False
    return True


# ---------- "not documented" must really be absent ----------
# English item/category keyword -> German terms that would prove it IS documented
MISSING_TERMS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"allerg|intoleran|\bcave\b", re.I), ["allergie", "allergisch", "unverträglich"]),
    (re.compile(r"anticoag|blood thinn|antiplatelet|aspirin|\bass\b", re.I),
     ["thrombo ass", "ass 100", "marcumar", "phenprocoumon", "xarelto", "eliquis", "clexane", "heparin", "clopidogrel"]),
    (re.compile(r"weight", re.I), ["gewicht"]),
    (re.compile(r"height", re.I), ["größe:"]),
    (re.compile(r"\bbmi\b", re.I), ["bmi"]),
    (re.compile(r"ejection|lvef|\bef\b|lv function|heart function|cardiac function|echo", re.I),
     ["lv-funktion", "(ef ", "echokardiographie"]),
    (re.compile(r"\becg\b|\bekg\b|rhythm", re.I), ["ekg", "sinusrhythmus"]),
    (re.compile(r"blood pressure|\bbp\b|hypertens", re.I), ["mmhg", "hypertonie"]),
    (re.compile(r"diabet|hba1c|glucose", re.I), ["diabetes", "hba1c", "dmii", "glucophage"]),
    (re.compile(r"receptor|\ber\b|\bpr\b|hormone", re.I), ["östrogenrezeptor", "progesteronrezeptor", "hormonrezeptor"]),
    (re.compile(r"her2", re.I), ["her2neu"]),
    (re.compile(r"ki-?67|proliferation", re.I), ["ki67"]),
    (re.compile(r"\btnm\b|staging|stage\b", re.I), ["t2 n0", "tnm"]),
    (re.compile(r"grad(e|ing)", re.I), ["g2", "grading"]),
    (re.compile(r"lymph node", re.I), ["lymphknoten"]),
    (re.compile(r"carotid|stenos", re.I), ["carotis", "karotis"]),
    (re.compile(r"kidney|renal|creatinin|egfr", re.I), ["kreatinin", "gfr", "niereninsuff"]),
    (re.compile(r"sleep apn|osa\b", re.I), ["schlafapnoe"]),
    (re.compile(r"reflux|gerd", re.I), ["reflux", "gerd"]),
    (re.compile(r"lupus|\bsle\b", re.I), ["lupus", "sle"]),
    (re.compile(r"osteopor|bone", re.I), ["osteoporose", "alendronat"]),
    (re.compile(r"surgery date|operation date|planned surgery|procedure date", re.I), ["quadrantenresektion", "iort"]),
    (re.compile(r"contrast", re.I), ["km-allergie", "kontrastmittel"]),
]


_EVENT = re.compile(r"took place|taken place|was (done|performed|carried out)|has been (done|performed)|"
                    r"\brecord (that|of)\b.*\b(done|performed|place|result)", re.I)


def find_documented(patient: Patient, category: str, item: str, extra: list[str]) -> list[str]:
    """Passage IDs that contradict a 'not documented' claim (empty list = really absent).
    Uses the hard-coded term table plus the model's own `searched` terms; headings
    ("Allergien:") alone do not count as evidence."""
    probe = f"{category} {item}"
    if _EVENT.search(item):     # "no record that X took place": mentions of planned X are not evidence
        return []
    terms = [t.lower() for pat, ts in MISSING_TERMS if pat.search(probe) for t in ts]
    terms += [t.lower() for t in extra if len(t) >= 4]
    if not terms:
        return []
    hits = []
    for ps in patient.passage_index.values():
        if ps.kind == "heading":
            continue
        low = " ".join(ps.text.lower().split())
        if any(t in low for t in terms):
            hits.append(ps.id)
    return hits


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
        if not polarity_ok(cf.value, quotes):
            rejected.append(Rejected(kind="fact", item=f"{cf.label}: {cf.value}", reason="positive/negative mismatch"))
            continue
        if not negation_ok(cf.value, quotes):
            rejected.append(Rejected(kind="fact", item=f"{cf.label}: {cf.value}", reason="negation mismatch"))
            continue
        checks.append("negation")
        if not certainty_ok(cf.value, quotes):
            rejected.append(Rejected(kind="fact", item=f"{cf.label}: {cf.value}",
                                     reason="source says suspected (Vd.a./Verdacht), value states it as confirmed"))
            continue
        checks.append("certainty")
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
        contras.append(Contradiction(id=f"c{i}", category=cc.category, summary=cc.summary, claims=claims,
                                     confidence=cc.confidence, origin=cc.origin))

    missing = [Missing(**m.model_dump()) for m in cand.not_documented]
    # a "not documented" item contradicted by a verified fact in the same category is dropped
    covered = {f.category for f in facts}
    kept = []
    for m in missing:
        if m.category in covered and any(m.item.lower() in f.label.lower() for f in facts if f.category == m.category):
            rejected.append(Rejected(kind="missing", item=m.item, reason="a verified fact covers it"))
            continue
        hits = find_documented(patient, m.category, m.item, m.searched)
        if hits:
            rejected.append(Rejected(kind="missing", item=m.item,
                                     reason=f"documented in {', '.join(hits[:3])}"))
            continue
        kept.append(m)
    return facts, contras, kept, rejected
