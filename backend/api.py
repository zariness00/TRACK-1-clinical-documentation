"""FastAPI app. Run:  uvicorn backend.api:app --reload --port 8000
Docs (try every endpoint in the browser): http://localhost:8000/docs
"""
from __future__ import annotations

import importlib
import os
from typing import Optional
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .context import llm_context
from .corpus import get_corpus
from .mock import mock_summarize
from .models import CandidateOutput, SummaryRequest, SummaryResponse
from .search import PassageIndex
from .validate import validate
from .verify import verify_quote

app = FastAPI(title="Track 1 — Clinical documentation backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_indexes: dict[str, PassageIndex] = {}


def _patient(pid: str):
    try:
        return get_corpus().patient(pid)
    except KeyError:
        raise HTTPException(404, f"unknown patient {pid}; loaded: {list(get_corpus().patients)}")


def _index(pid: str) -> PassageIndex:
    if pid not in _indexes:
        _indexes[pid] = PassageIndex(_patient(pid))
    return _indexes[pid]


def _summarizer():
    """Use Zarina's backend/ai.py:summarize(patient, role, task, context) -> CandidateOutput
    when it exists and MOCK != 1; otherwise the mock."""
    if os.environ.get("MOCK") == "1":
        return None
    try:
        return importlib.import_module("backend.ai").summarize
    except ModuleNotFoundError:
        return None


@app.get("/api/health")
def health():
    c = get_corpus()
    return {"ok": True, "data_dir": str(c.data_dir),
            "patients": {pid: len(p.documents) for pid, p in c.patients.items()},
            "summarizer": "ai" if _summarizer() else "mock"}


@app.get("/api/patients")
def patients():
    return [{"patient_id": p.patient_id, "name": p.name, "n_documents": len(p.documents)}
            for p in get_corpus().patients.values()]


@app.get("/api/patients/{pid}")
def patient(pid: str):
    return _patient(pid).meta()


@app.get("/api/patients/{pid}/documents/{doc_id}")
def document(pid: str, doc_id: str):
    """Full raw text + passages. All `start`/`end` offsets anywhere in the API index into `text`."""
    p = _patient(pid)
    if doc_id not in p.documents:
        raise HTTPException(404, f"unknown document {doc_id}")
    d = p.documents[doc_id]
    return {**d.meta(), "text": d.text, "passages": [ps.to_dict() for ps in d.passages]}


@app.get("/api/patients/{pid}/passages")
def passages(pid: str, q: Optional[str] = None, doc_id: Optional[str] = None, k: int = Query(20, le=500)):
    p = _patient(pid)
    if q:
        return [{"score": round(s, 3), **ps.to_dict()} for s, ps in _index(pid).search(q, k, doc_id)]
    items = [ps for ps in p.passage_index.values() if not doc_id or ps.doc_id == doc_id]
    return [ps.to_dict() for ps in items[:k]]


@app.get("/api/patients/{pid}/context")
def context(pid: str):
    """Prompt-ready text of all documents with passage IDs (for the AI calls)."""
    return {"context": llm_context(_patient(pid))}


@app.get("/api/patients/{pid}/extractions")
def extractions(pid: str):
    """IBM JSON files — hints only. Where they differ from the documents, documents win."""
    return _patient(pid).extractions


class VerifyIn(BaseModel):
    patient_id: str = "0000-0001"
    doc_id: Optional[str] = None
    quote: str


@app.post("/api/verify")
def verify(body: VerifyIn):
    return verify_quote(_patient(body.patient_id), body.doc_id, body.quote).to_dict()


@app.post("/api/validate", response_model=SummaryResponse)
def validate_candidates(cand: CandidateOutput, patient_id: str = "0000-0001", role: str = "", task: str = ""):
    """Zarina: POST your raw model JSON here to see what survives verification and why."""
    p = _patient(patient_id)
    t0 = time.perf_counter()
    facts, contras, missing, rejected = validate(p, cand)
    return SummaryResponse(patient_id=p.patient_id, patient_name=p.name, role=role, task=task,
                           categories=cand.categories, facts=facts, not_documented=missing,
                           contradictions=contras, rejected=rejected,
                           timings_ms={"validate": round((time.perf_counter() - t0) * 1000, 1)})


@app.post("/api/summary", response_model=SummaryResponse)
def summary(req: SummaryRequest):
    t0 = time.perf_counter()
    p = _patient(req.patient_id)
    role, task = req.role.strip(), req.task.strip()
    if not (role or task) and req.situation:
        task = req.situation.strip()
    fn = _summarizer()
    if fn:
        cand = fn(p, role, task, llm_context(p))
        if isinstance(cand, dict):
            cand = CandidateOutput.model_validate(cand)
    else:
        cand = mock_summarize(p, role, task)
    t1 = time.perf_counter()
    facts, contras, missing, rejected = validate(p, cand)
    t2 = time.perf_counter()
    return SummaryResponse(
        patient_id=p.patient_id, patient_name=p.name, role=role, task=task,
        categories=cand.categories, facts=facts, not_documented=missing,
        contradictions=contras, rejected=rejected, mock=fn is None,
        timings_ms={"model": round((t1 - t0) * 1000, 1), "validate": round((t2 - t1) * 1000, 1),
                    "total": round((t2 - t0) * 1000, 1)},
    )
