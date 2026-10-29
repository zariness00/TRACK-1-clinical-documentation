"""Mock summarizer: keyword rules -> CandidateOutput, run through the SAME validator
as the real model. Gives Gregor real, clickable source links before the AI is ready.
Zarina replaces this with backend/ai.py:summarize(...) returning CandidateOutput.
"""
from __future__ import annotations

from .corpus import Patient
from .models import (CandidateClaim, CandidateContradiction, CandidateFact, CandidateMissing,
                     CandidateOutput, CandidateSource)
from .search import PassageIndex

# category -> (English label, German search terms)
CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "demographics":     ("Age / sex", ["jährige", "geb", "Alter"]),
    "anticoagulation":  ("Blood thinners", ["ASS", "Aspirin", "Clopidogrel", "Marcumar", "Phenprocoumon",
                                            "Xarelto", "Rivaroxaban", "Apixaban", "Eliquis", "Heparin",
                                            "Clexane", "Enoxaparin", "Antikoagul"]),
    "allergies":        ("Allergies", ["Allergie", "Unverträglichkeit", "allergisch"]),
    "cardiac":          ("Heart function", ["Echokardiographie", "LVEF", "Ejektionsfraktion", "EKG",
                                            "kardial", "Herz"]),
    "medication":       ("Current medication", ["Medikation", "mg", "Tbl"]),
    "diagnosis":        ("Diagnoses", ["Diagnose", "Karzinom", "Mammakarzinom", "ICD"]),
    "tumour_biology":   ("Receptor status / grading", ["Östrogenrezeptor", "Progesteronrezeptor",
                                                       "Her2neu", "Ki67", "Hormonrezeptor", "Allred"]),
    "procedures":       ("Procedures", ["Operation", "OP", "Mastektomie", "Ablatio", "BET", "Sentinel"]),
    "labs":             ("Laboratory", ["Hb", "Kreatinin", "Leukozyten", "Thrombozyten", "INR", "Labor"]),
    "vitals_body":      ("Height / weight / BMI", ["Größe", "Gewicht", "BMI", "kg"]),
    "follow_up":        ("Follow-up / plan", ["Nachsorge", "Kontrolle", "Empfehlung", "Wiedervorstellung"]),
}

# role/task keyword -> categories (hard-coding this is explicitly allowed by the brief)
ROUTES: list[tuple[list[str], list[str]]] = [
    (["anaesth", "anesth", "pre-op", "preop", "narkose"],
     ["demographics", "anticoagulation", "allergies", "cardiac", "medication", "vitals_body", "labs"]),
    (["oncolog", "tumour board", "tumor board", "chemo"],
     ["diagnosis", "tumour_biology", "procedures", "medication", "follow_up"]),
    (["gp", "general practitioner", "hausarzt", "discharge", "follow"],
     ["diagnosis", "medication", "follow_up", "allergies"]),
    (["pharmac", "medication review"],
     ["medication", "anticoagulation", "allergies", "labs"]),
    (["nurse", "pflege"],
     ["allergies", "medication", "procedures", "vitals_body"]),
]
DEFAULT = ["diagnosis", "medication", "allergies", "procedures"]


def plan(role: str, task: str) -> list[str]:
    q = f"{role} {task}".lower()
    cats: list[str] = []
    for keys, cs in ROUTES:
        if any(k in q for k in keys):
            cats += [c for c in cs if c not in cats]
    return cats or DEFAULT


def mock_summarize(patient: Patient, role: str, task: str, per_category: int = 2) -> CandidateOutput:
    idx = PassageIndex(patient)
    cats = plan(role, task)
    out = CandidateOutput(categories=cats)
    for c in cats:
        label, terms = CATEGORIES[c]
        hits = [h for h in idx.search(" ".join(terms), k=10) if h[1].kind != "heading"][:per_category]
        if not hits:
            out.not_documented.append(CandidateMissing(category=c, item=label, searched=terms))
            continue
        for _, p in hits:
            out.facts.append(CandidateFact(
                category=c, label=f"[MOCK] {label}", value=p.text[:200],
                sources=[CandidateSource(doc_id=p.doc_id, passage_id=p.id, quote=p.text)]))
    # one mock contradiction so the UI has something to render: same topic, two documents
    hits = [h for h in idx.search("BMI", k=10) if h[1].kind != "heading"]
    docs_seen, claims = set(), []
    for _, p in hits:
        if p.doc_id not in docs_seen:
            docs_seen.add(p.doc_id)
            claims.append(CandidateClaim(statement=p.text[:120],
                                         source=CandidateSource(doc_id=p.doc_id, quote=p.text)))
        if len(claims) == 2:
            break
    if len(claims) == 2:
        out.contradictions.append(CandidateContradiction(
            category="vitals_body", summary="[MOCK] Body measurements differ between documents", claims=claims))
    return out
