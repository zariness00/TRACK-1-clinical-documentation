"""Rule-based contradiction finder. Runs once at start-up over all documents.

A contradiction = passages that CANNOT all be true (brief). Each rule is
deliberately conservative, because every false alarm costs a point:

  R1 bmi        BMI stated in a letter differs by > 1 kg/m² from weight/height² in the same letter
  R2 dates      one letter gives two different dates for the same admission / discharge
  R3 side       the same finding (organ + same %) is recorded as left in one passage and right in another
  R4 presence   a finding is stated as present in one passage and absent ("keine …") in another
                (confidence "medium": could also be a change between examinations)
  R5 procedure  a pathology report's sampling date ("Entnahmedatum") differs from the date of the
                section in another document that says the same procedure was performed
                ("Ultraschalluntersuchung … vom 12.04.2016: … Es erfolgte die … Biopsie") (medium)

Not flagged on purpose (brief: false alarms): small measurement differences,
values that change over time (weight, doses), differences from IBM's JSON,
clinical questions ("Fragestellung: …?").

Every rule outputs CandidateContradiction with verbatim quotes, so the result
goes through the same validator as AI output (quotes must exist, >= 2 passages).
"""
from __future__ import annotations

import re
from datetime import date

from .corpus import Document, Patient
from .models import CandidateClaim, CandidateContradiction, CandidateSource

BMI_TOLERANCE = 1.0   # kg/m²; covers rounding and slightly different measuring days

_NUM = r"(\d{1,3}(?:[.,]\d{1,2})?)"
_HEIGHT = re.compile(r"(?:Größe|Körpergröße|Groesse)\s*:?\s*(\d{3})\s*cm|(?:Größe|Körpergröße)\s*:?\s*(1[.,]\d{1,2})\s*m\b", re.I)
_WEIGHT = re.compile(r"(?:Gewicht|Körpergewicht)\s*:?\s*" + _NUM + r"\s*kg", re.I)
_BMI = re.compile(r"\bBMI\s*:?\s*" + _NUM + r"(?![\d.,]*\d)", re.I)

_DATE = r"(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)"
# header form "Aufnahme am …" and narrative form "… am <date> … aufgenommen" (lower-case verb,
# never across a "|" or a line break, so "Aufnahme am X | Entlassen am Y" is two different events)
_ADMIT = [re.compile(r"\bAufnahme\s+am\s+" + _DATE, re.I),
          re.compile(r"\bam\s+" + _DATE + r"[^.!?\n|]{0,200}?\b(?-i:aufgenommen)\b", re.I)]
_DISCHARGE = [re.compile(r"\bEntlassen\s+am\s+" + _DATE, re.I),
              re.compile(r"\bEntlassung\s+am\s+" + _DATE, re.I),
              re.compile(r"\bam\s+" + _DATE + r"[^.!?\n|]{0,200}?\b(?-i:entlassen)\b", re.I)]

_LEFT = re.compile(r"\b(links|linke[nrsm]?|li\.?|sin\.?|sinistra)(?=\W|$)", re.I)
_RIGHT = re.compile(r"\b(rechts|rechte[nrsm]?|re\.?|dex\.?|dextra)(?=\W|$)", re.I)
_BOTH = re.compile(r"\b(beidseits|bds\.?|bilateral)(?=\W|$)", re.I)
_PCT = re.compile(r"(\d{1,3})\s?%")
ORGANS = {
    "carotid artery": r"karotis|carotis",
    "breast": r"\bmamma\b|\bbrust\b",
    "kidney": r"\bniere",
    "lung": r"\blunge",
    "ovary": r"\bovar",
    "eye": r"\bauge\b",
    "vertebral artery": r"vertebralis",
}

# R4: finding -> (present pattern, absent pattern). Absent = explicit negation of the same finding.
FINDINGS = {
    "enlarged axillary lymph nodes": (
        r"vergrößerte\w*\s+lymphknoten[^.]{0,30}axill|axill\w*[^.]{0,30}vergrößerte\w*\s+lymphknoten|"
        r"lymphknoten\w*[^.]{0,20}vergrößert[^.]{0,30}axill",
        r"\bkeine?\b[^.]{0,30}vergrößerte\w*\s+lymphknoten"),
    "pericardial effusion": (r"perikarderg(u|ü)ss?ß?", r"\bkein\w*\s+perikarderg"),
    "pleural effusion": (r"pleuraerg(u|ü)ss?ß?", r"\bkein\w*\s+pleuraerg"),
    "intracranial bleeding": (r"\b(hirn)?blutung\b", r"\bkeine?\b[^.]{0,40}\bblutung"),
}


def _f(x: str) -> float:
    return float(x.replace(",", "."))


def _claim(doc: Document, s: int, e: int, statement: str) -> CandidateClaim:
    return CandidateClaim(statement=statement, source=CandidateSource(doc_id=doc.doc_id, quote=doc.text[s:e]))


def _passages(doc: Document):
    return [p for p in doc.passages if p.kind != "heading"]


# ---------------------------------------------------------------- R1 BMI
def rule_bmi(patient: Patient) -> list[CandidateContradiction]:
    out = []
    for doc in patient.documents.values():
        t = doc.text
        heights = [(m.start(), m.end(), _f(m.group(1)) / 100 if m.group(1) else _f(m.group(2))) for m in _HEIGHT.finditer(t)]
        weights = [(m.start(), m.end(), _f(m.group(1))) for m in _WEIGHT.finditer(t)]
        for b in _BMI.finditer(t):
            bmi = _f(b.group(1))
            near = lambda xs: min(xs, key=lambda x: abs(x[0] - b.start())) if xs else None
            h, w = near(heights), near(weights)
            if not h or not w or abs(h[0] - b.start()) > 400 or abs(w[0] - b.start()) > 400:
                continue
            if not (1.2 <= h[2] <= 2.3 and 25 <= w[2] <= 300):
                continue
            calc = w[2] / h[2] ** 2
            if abs(calc - bmi) > BMI_TOLERANCE:
                out.append(CandidateContradiction(
                    category="vitals_body", origin="rules",
                    summary=(f"BMI {b.group(1)} does not match height {round(h[2] * 100)} cm and weight "
                             f"{w[2]:g} kg in the same document (calculated BMI {calc:.1f})"),
                    claims=[_claim(doc, h[0], h[1], f"Height {round(h[2] * 100)} cm"),
                            _claim(doc, w[0], w[1], f"Weight {w[2]:g} kg"),
                            _claim(doc, b.start(), b.end(), f"Stated BMI {b.group(1)} (calculated {calc:.1f})")]))
    return out


# ---------------------------------------------------------------- R2 dates
def _parse(d, m, y):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def rule_dates(patient: Patient) -> list[CandidateContradiction]:
    out = []
    for doc in patient.documents.values():
        for event, pats in (("admission", _ADMIT), ("discharge", _DISCHARGE)):
            found: dict[date, tuple[int, int]] = {}
            for pat in pats:
                for m in pat.finditer(doc.text):
                    dt = _parse(*m.groups()[:3])
                    if dt and dt not in found:
                        found[dt] = (m.start(), m.end())
            if len(found) >= 2:
                items = sorted(found.items())
                out.append(CandidateContradiction(
                    category="dates", origin="rules",
                    summary=f"Two different {event} dates in the same document: "
                            + " vs ".join(d.strftime("%d.%m.%Y") for d, _ in items),
                    claims=[_claim(doc, s, e, f"{event.capitalize()} on {d.strftime('%d.%m.%Y')}")
                            for d, (s, e) in items]))
    return out


# ---------------------------------------------------------------- R3 side
def _side(text: str) -> str | None:
    if _BOTH.search(text):
        return None
    l, r = bool(_LEFT.search(text)), bool(_RIGHT.search(text))
    return "left" if l and not r else "right" if r and not l else None


def rule_side(patient: Patient) -> list[CandidateContradiction]:
    groups: dict[tuple[str, str], dict[str, list]] = {}
    for doc in patient.documents.values():
        for p in _passages(doc):
            if "?" in p.text:
                continue
            side = _side(p.text)
            if not side:
                continue
            for organ, pat in ORGANS.items():
                if re.search(pat, p.text, re.I):
                    for pct in set(_PCT.findall(p.text)):
                        groups.setdefault((organ, pct), {}).setdefault(side, []).append((doc, p))
    out = []
    for (organ, pct), sides in groups.items():
        if "left" in sides and "right" in sides:
            minority, majority = sorted(("left", "right"), key=lambda s: len(sides[s]))
            claims = [_claim(d, p.start, p.end, f"{organ} finding ({pct}%) on the {minority} side")
                      for d, p in sides[minority][:2]]
            # majority side: prefer passages from OTHER documents first (e.g. the original report), max 2
            maj = sorted(sides[majority], key=lambda dp: dp[0].doc_id in {d.doc_id for d, _ in sides[minority]})
            claims += [_claim(d, p.start, p.end, f"{organ} finding ({pct}%) on the {majority} side") for d, p in maj[:2]]
            out.append(CandidateContradiction(
                category="laterality", origin="rules",
                summary=f"{organ.capitalize()} finding ({pct}%) is documented as {minority} in "
                        f"{len(sides[minority])} passage(s) and as {majority} in {len(sides[majority])}",
                claims=claims))
    return out


# ---------------------------------------------------------------- R4 presence vs absence
def rule_presence(patient: Patient) -> list[CandidateContradiction]:
    out = []
    for finding, (present, absent) in FINDINGS.items():
        pos, neg = [], []
        for doc in patient.documents.values():
            for p in _passages(doc):
                if "?" in p.text or "fragestellung" in p.text.lower():
                    continue
                txt = " ".join(p.text.split())
                if re.search(absent, txt, re.I):
                    neg.append((doc, p))
                elif re.search(present, txt, re.I) and not re.search(r"\b(kein|keine|ohne|nicht)\b", txt, re.I):
                    pos.append((doc, p))
        if pos and neg:
            out.append(CandidateContradiction(
                category="findings", origin="rules", confidence="medium",
                summary=f"{finding.capitalize()}: reported as present in one document and as absent in another",
                claims=[_claim(pos[0][0], pos[0][1].start, pos[0][1].end, f"{finding}: present"),
                        _claim(neg[0][0], neg[0][1].start, neg[0][1].end, f"{finding}: absent")]))
    return out


# ---------------------------------------------------------------- R5 procedure date across documents
PROCEDURES = {"biopsy": r"biops|stanz"}
_SAMPLED = re.compile(r"\bEntnahme(?:datum)?\s*:?\s*" + _DATE, re.I)
_PERFORMED = re.compile(r"\b(erfolgte|durchgeführt|vorgenommen|entnommen|gewonnen)\b", re.I)
_PLANNED = re.compile(r"\b(geplant|vorgesehen|empfohlen|terminisiert)\b", re.I)
_DATED_HEADING = re.compile(r"\bvom\s+" + _DATE, re.I)
_SUBHEADINGS = re.compile(r"^(zusammenfassung|befund|beurteilung|ergebnis|technik)\s*:?$", re.I)
_REPORT_HEADING = re.compile(r"histolog|patholog|zytolog", re.I)


def _section_date(doc: Document, idx: int):
    """Date of the section a passage belongs to: nearest preceding heading 'X vom <date>:',
    skipping generic sub-headings ('Zusammenfassung:'). Returns (passage, date) or None."""
    for q in reversed(doc.passages[:idx]):
        if q.kind != "heading":
            continue
        if _SUBHEADINGS.match(q.text.strip()):
            continue
        m = _DATED_HEADING.search(q.text)
        if not m or _REPORT_HEADING.search(q.text):    # "Histologie vom 13.04." is the report date
            return None
        dt = _parse(*m.groups())
        return (q, dt) if dt else None
    return None


def rule_procedure_dates(patient: Patient) -> list[CandidateContradiction]:
    out = []
    for proc, kw in PROCEDURES.items():
        sampled = []                                     # (doc, start, end, date) from pathology reports
        for doc in patient.documents.values():
            if not re.search(kw, doc.text, re.I):
                continue
            for m in _SAMPLED.finditer(doc.text):
                dt = _parse(*m.groups())
                if dt:
                    sampled.append((doc, m.start(), m.end(), dt))
        for sdoc, ss, se, sdate in sampled:
            for doc in patient.documents.values():
                if doc is sdoc:
                    continue
                for i, p in enumerate(doc.passages):
                    if p.kind == "heading" or not re.search(kw, p.text, re.I):
                        continue
                    if not _PERFORMED.search(p.text) or _PLANNED.search(p.text):
                        continue
                    sec = _section_date(doc, i)
                    if sec and sec[1] != sdate:
                        hp, hdate = sec
                        out.append(CandidateContradiction(
                            category="dates", origin="rules", confidence="medium",
                            summary=(f"{proc.capitalize()} date differs: {sdate.strftime('%d.%m.%Y')} in the pathology "
                                     f"report vs {hdate.strftime('%d.%m.%Y')} in {doc.title or doc.doc_id}"),
                            claims=[_claim(sdoc, ss, se, f"{proc.capitalize()} sample taken on {sdate.strftime('%d.%m.%Y')}"),
                                    _claim(doc, hp.start, hp.end, f"Examination dated {hdate.strftime('%d.%m.%Y')}"),
                                    _claim(doc, p.start, p.end, f"{proc.capitalize()} performed during that examination")]))
                        break
    return out


RULES = (rule_bmi, rule_dates, rule_side, rule_presence, rule_procedure_dates)


def find_contradictions(patient: Patient) -> list[CandidateContradiction]:
    out: list[CandidateContradiction] = []
    for rule in RULES:
        out += rule(patient)
    return out
