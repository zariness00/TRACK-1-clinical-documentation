"""THE CONTRACT between backend (Veronika), AI (Zarina) and UI (Gregor).

Zarina's model returns `Candidate*` objects (doc_id/passage_id + quote only).
`validate.py` turns them into `Fact`/`Contradiction` with verified offsets,
or rejects them. Gregor renders only `SummaryResponse`.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ---------- request ----------
class SummaryRequest(BaseModel):
    patient_id: str = "0000-0001"
    role: str = Field("", examples=["Anaesthetist"])
    task: str = Field("", examples=["Pre-operative check before breast surgery"])
    situation: Optional[str] = Field(None, description="Free text (title + description). Used if role/task empty.")


# ---------- what the model is allowed to return ----------
class CandidateSource(BaseModel):
    doc_id: Optional[str] = None
    passage_id: Optional[str] = None      # e.g. "doc3#p12" — optional, helps disambiguate
    quote: str                         # verbatim from the document (German)


class CandidateFact(BaseModel):
    category: str                      # from the planner's category list, e.g. "anticoagulation"
    label: str                         # short English label, e.g. "Blood thinner"
    value: str                         # English, e.g. "ASS 100 mg 1-0-0"
    date: Optional[str] = None            # ISO date of the fact if known
    derived: bool = False              # true for calculated values (weight change, BMI recomputed)
    sources: list[CandidateSource]


class CandidateClaim(BaseModel):
    statement: str
    source: CandidateSource


class CandidateContradiction(BaseModel):
    category: str = "general"
    summary: str                       # "Two different dates for the mastectomy"
    claims: list[CandidateClaim]


class CandidateMissing(BaseModel):
    category: str
    item: str                          # "Allergies"
    searched: list[str] = []           # terms / documents checked


class CandidateOutput(BaseModel):
    categories: list[str] = []
    facts: list[CandidateFact] = []
    not_documented: list[CandidateMissing] = []
    contradictions: list[CandidateContradiction] = []


# ---------- what the UI renders (verified only) ----------
class Source(BaseModel):
    doc_id: str
    doc_title: str
    doc_date: Optional[str]
    quote: str                         # exact raw text of the span(s)
    start: int                         # raw char offsets in GET /documents/{doc_id} text
    end: int
    spans: list[tuple[int, int]]       # >1 when the quote had an ellipsis
    passage_ids: list[str]
    match: str                         # exact | normalized | caseless | wrong_doc (auto-corrected)


class Fact(BaseModel):
    id: str
    category: str
    label: str
    value: str
    date: Optional[str] = None
    derived: bool = False
    sources: list[Source]
    checks: list[str] = []             # which guards passed, e.g. ["quote", "numbers", "laterality"]


class Claim(BaseModel):
    statement: str
    source: Source


class Contradiction(BaseModel):
    id: str
    category: str
    summary: str
    claims: list[Claim]


class Missing(BaseModel):
    category: str
    item: str
    searched: list[str] = []


class Rejected(BaseModel):
    kind: str                          # fact | contradiction | source
    item: str
    reason: str


class SummaryResponse(BaseModel):
    patient_id: str
    patient_name: str
    role: str
    task: str
    categories: list[str]
    facts: list[Fact]
    not_documented: list[Missing]
    contradictions: list[Contradiction]
    rejected: list[Rejected] = []      # for debugging/metrics, not shown to clinicians
    timings_ms: dict[str, float] = {}
    mock: bool = False
