"""AI orchestration: hybrid planner -> 3 parallel workers -> reconciliation -> (optional) semantic verifier.

    summarize(patient, role, task, context) -> CandidateOutput      (api.py then runs validate.py)

Workers: summary (facts + quotes), missing-info (is each need documented?), contradictions (patient-wide,
cached per patient, started immediately so it overlaps the planner). All models are OpenAI-compatible
(Nebius) and configurable via env; the API key is read from NEBIUS_API_KEY and never logged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import httpx

from .corpus import Patient
from .models import (CandidateClaim, CandidateContradiction, CandidateFact, CandidateMissing,
                     CandidateOutput, CandidateSource)
from .verify import verify_quote

log = logging.getLogger("backend.ai")


def _load_dotenv(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#") and v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_dotenv()
KEY = os.environ.get("NEBIUS_API_KEY") or os.environ.get("LLM_API_KEY")
ENABLED = bool(KEY)
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.tokenfactory.us-central1.nebius.com/v1").rstrip("/")
FAST = "deepseek-ai/DeepSeek-V4-Flash-0731"
MODELS = {
    "planner": os.environ.get("LLM_MODEL_PLANNER", FAST),
    "summary": os.environ.get("LLM_MODEL_SUMMARY", FAST),
    "missing": os.environ.get("LLM_MODEL_MISSING", FAST),
    "contras": os.environ.get("LLM_MODEL_CONTRADICTIONS", "openai/gpt-oss-120b"),
    "verifier": os.environ.get("LLM_MODEL_VERIFIER", FAST),
}
VERIFY = os.environ.get("LLM_VERIFY", "0") == "1"
# Reasoning per role: off | low | medium | high. Measured: DeepSeek with reasoning exhausts the budget before
# answering on the full record (>8 s, no JSON), so the critical path runs without it; the contradiction worker
# (background, cached per patient) keeps bounded reasoning.
REASONING = {r: os.environ.get(f"LLM_REASONING_{r.upper()}", d) for r, d in
             {"planner": "off", "summary": "off", "missing": "off", "contras": "medium", "verifier": "off"}.items()}
PLANNER_TIMEOUT_S = float(os.environ.get("LLM_PLANNER_TIMEOUT_S", "3"))
WORKER_TIMEOUT_S = float(os.environ.get("LLM_WORKER_TIMEOUT_S", "8"))
CONTRA_TIMEOUT_S = float(os.environ.get("LLM_CONTRA_TIMEOUT_S", "90"))
MAX_TOKENS = {"plan": 800, "summary": 4000, "missing": 4000, "contras": 8000, "verify": 1500}
TODAY = "15 April 2016"

_pool = ThreadPoolExecutor(8)
_http = httpx.Client(timeout=30)
_contra_cache: dict[str, Future] = {}
_contra_lock = threading.Lock()
last_timings: dict[str, float] = {}

# ---------------------------------------------------------------- hybrid planner: checklists (allowed by the brief)
# (keywords in role/task, [(category, need)])
CHECKLISTS: list[tuple[list[str], list[tuple[str, str]]]] = [
    (["anaesth", "anesth", "pre-op", "preop", "pre-operative", "preoperative"], [
        ("demographics", "Age and sex"),
        ("antithrombotics", "Current antithrombotic / anticoagulant drug and dose"),
        ("allergies", "Documented allergies and reactions"),
        ("cardiac_function", "Last documented heart function (LVEF, echocardiography, ECG)"),
        ("current_medication", "Current medication with doses"),
        ("body_measurements", "Height, weight and BMI"),
        ("comorbidities", "Relevant comorbidities (diabetes, hypertension, sleep apnoea, reflux, lung disease)"),
        ("kidney_function", "Kidney function (creatinine / eGFR)")]),
    (["surgeon", "surgery planning", "tumour board", "tumor board", "oncolog"], [
        ("histology_tnm", "Tumour histology and TNM stage / grading"),
        ("receptor_status", "Hormone receptor status, HER2 and Ki-67"),
        ("tumour_board", "Tumour board recommendation and scheduled date of surgery"),
        ("tumour_location_size", "Tumour side, location and size"),
        ("lymph_nodes", "Axillary lymph node findings"),
        ("antithrombotics", "Current antithrombotic / anticoagulant drug and dose"),
        ("allergies", "Documented allergies")]),
    (["diabet", "glucose", "hba1c", "metabolic"], [
        ("glucose_lowering_drugs", "Current glucose-lowering drugs with doses"),
        ("hba1c", "Latest HbA1c value and its date"),
        ("weight_trend", "Weight over time (all documented weights with dates)"),
        ("kidney_function", "Kidney function (creatinine / eGFR)"),
        ("cv_risk", "Other cardiovascular risk factors and their treatment (BP, lipids)")]),
    (["emergency", "night duty", "dizziness", "vision", "stroke", "neurolog"], [
        ("previous_episodes", "Previous similar episodes and the cause found"),
        ("contrast_allergy", "Contrast media allergy or previous contrast reaction"),
        ("metformin", "Metformin intake (relevant before iodinated contrast)"),
        ("kidney_function", "Kidney function (creatinine / eGFR)"),
        ("antihypertensives", "Current antihypertensive drug and dose"),
        ("antithrombotics", "Current antithrombotic / anticoagulant drug and dose"),
        ("vascular_findings", "Carotid / vascular findings (side and degree)"),
        ("allergies", "Documented allergies")]),
    (["follow-up", "follow up", "followup", "discharge", "still open"], [
        ("recommended_follow_ups", "Every follow-up recommended at discharge, one fact each (imaging, specialist "
                                   "check-ups, blood pressure or lab monitoring, medication adjustment by the GP)"),
        ("follow_up_done", "For each recommended follow-up: is there a later document showing it took place?"),
        ("vascular_findings", "Carotid stenosis side and degree"),
        ("current_medication", "Current medication with doses")]),
    (["gp", "general practitioner", "family doctor", "hausarzt"], [
        ("diagnoses", "Active diagnoses"),
        ("current_medication", "Current medication with doses")]),
]
DEFAULT_NEEDS = [("diagnoses", "Active diagnoses"), ("current_medication", "Current medication with doses"),
                 ("allergies", "Documented allergies")]


def checklist_needs(role: str, task: str) -> list[dict]:
    q = f" {role} {task} ".lower()
    seen, needs = set(), []
    for keys, items in CHECKLISTS:
        if any(k in q for k in keys):
            for cat, need in items:
                if cat not in seen:
                    seen.add(cat)
                    needs.append({"category": cat, "need": need, "source": "checklist"})
    return needs or [{"category": c, "need": n, "source": "checklist"} for c, n in DEFAULT_NEEDS]


# ---------------------------------------------------------------- prompts + schemas
S, B = {"type": "string"}, {"type": "boolean"}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


_SOURCE = _obj({"doc_id": S, "passage_id": S, "quote": S})
SCHEMAS = {
    "plan": _obj({"add": _arr(_obj({"category": S, "need": S})), "drop": _arr(S)}),
    "summary": _obj({"facts": _arr(_obj({"need_id": S, "label": S, "value": S, "date": S, "derived": B,
                                         "sources": _arr(_SOURCE)}))}),
    "missing": _obj({"needs": _arr(_obj({"need_id": S, "documented": B, "searched": _arr(S), "label": S,
                                         "value": S, "sources": _arr(_SOURCE)}))}),
    "contras": _obj({"contradictions": _arr(_obj({"category": S, "summary": S,
                                                  "claims": _arr(_obj({"statement": S, "source": _SOURCE}))}))}),
    "verify": _obj({"facts": _arr(_obj({"id": S, "supported": B})), "contradictions": _arr(_obj({"id": S, "real": B}))}),
}

CITE = ("Each source: doc_id and passage_id exactly as written in the brackets (e.g. 12340012#p36), and quote = a "
        "verbatim copy of German text from that passage (whole passage or a contiguous part, >= 10 characters).")

PLAN_SYS = f"""You plan which patient facts a clinician needs for one task. Latest document: {TODAY}.
You get the situation and a checklist of information needs. Add at most 3 needs the checklist misses that are
clinically important for THIS situation AND typically written in hospital letters (diagnoses, findings, medication,
allergies, procedures, plans). Do not add bedside items that letters do not contain (airway exam, fasting status,
contact details, current symptoms). Add nothing if the checklist is complete. Categories are short snake_case.
Return JSON only: {{"add": [{{"category", "need"}}], "drop": []}}"""

SUMMARY_SYS = f"""You extract facts for a clinician from the German clinical documents of one patient.
Today is {TODAY} (date of the latest document). Search ALL documents for each information need.
- Use only the documents. Never add knowledge that is not in them. Skip needs you cannot answer.
- One fact per distinct item: one per drug, one per measurement or finding. need_id = the id of the need it
  answers (e.g. "n3"). A drug that answers a specific need (e.g. the antithrombotic) gets its own fact for that need.
- label: short English; value: concise English (translate German terms; brand name plus generic name for drugs),
  keep numbers, doses, schedules (1-0-1) and left/right exactly.
- Only facts that are present. Never output a fact saying something is "not documented" / "not specified".
- Do NOT put dates in value (the date field and the source document show them), unless the date is the answer
  itself (e.g. date of a planned operation) and is in the quote.
- date: ISO yyyy-mm-dd of the finding if known, else "". derived: true only for values you calculated (e.g. weight change).
- Every number in value must appear in the quotes unless derived is true.
- For current status ("does she take"), use the latest document that mentions it and give its date.
- {CITE}
Return JSON only: {{"facts": [{{"need_id", "label", "value", "date", "derived", "sources": [{{"doc_id", "passage_id", "quote"}}]}}]}}"""

MISSING_SYS = f"""You check, for each information need, whether it is documented ANYWHERE in the German clinical
documents of one patient (latest document {TODAY}). Search all documents, including tables, headings and abbreviations
(e.g. KM-Allergie, ASS, LVEF/EF, Krea/GFR).
For every need return: need_id (e.g. "n3"), documented (true/false), searched (at most 3 German terms you checked),
and if documented: label, value (short English answer, no dates, every number must be in the quote) and ONE source
with the evidence; if not documented: label = exactly what is missing, specific (e.g. "No record that the carotid
duplex follow-up took place"), value = "", sources = []. Only mark documented when you can quote the evidence.
Keep it short.
- {CITE}
Return JSON only: {{"needs": [{{"need_id", "documented", "searched", "label", "value", "sources": [{{"doc_id", "passage_id", "quote"}}]}}]}}"""

CONTRA_SYS = f"""You check the German clinical documents of one patient for genuine contradictions: two or more passages
that cannot all be true, e.g. two different dates for the same event, left vs right for the same finding, a BMI that
does not match the height and weight in the same document, a finding stated as present in one place and absent in
another, a wrong case number or impossible date.
NOT contradictions: small measurement differences (e.g. 170 vs 171 cm), values that changed over time (weight, BMI or
doses between visits or stays), different wording or level of detail. Check the dates before calling something a
contradiction. Check the whole record. Report only contradictions you are confident about.
Each contradiction: category, summary (one English sentence naming the conflicting values), claims (2 or more),
each with statement (English) and source. Every claim cites exactly ONE passage (one bracket ID, never a range) and
quotes only from that passage; claims must cite different passages (e.g. a BMI mismatch = separate claims for the
height, the weight and the BMI passage). {CITE}
Return JSON only: {{"contradictions": [{{"category", "summary", "claims": [{{"statement", "source": {{"doc_id", "passage_id", "quote"}}}}]}}]}}"""

VERIFY_SYS = """You are a strict clinical fact checker. For each fact: supported = true only if the value is fully
supported by its German quotes (translation, generic drug names, schedule reformatting like "1 0 1 0" = 1-0-1 and
simple marked calculations are fine; any extra detail is not). For each contradiction: real = true only if the quoted
passages cannot all be true (not measurement noise, not changes over time).
Return JSON only, covering every id: {"facts": [{"id", "supported"}], "contradictions": [{"id", "real"}]}"""


# ---------------------------------------------------------------- LLM call
def _params(role: str) -> dict:
    effort = REASONING[role]
    if effort == "off":
        return {"chat_template_kwargs": {"thinking": False, "enable_thinking": False}}
    return {"reasoning_effort": effort}


def _parse(content: str):
    c = re.sub(r"(?s)<think>.*?</think>", "", content or "").strip()
    c = re.sub(r"^```(?:json)?\s*|\s*```$", "", c)
    try:
        return json.loads(c)
    except json.JSONDecodeError:
        i, j = c.find("{"), c.rfind("}")
        return json.loads(c[i:j + 1]) if 0 <= i < j else None


def _chat(role: str, schema: str, system: str, user: str, timeout: float):
    model = MODELS[role]
    body = {"model": model, "temperature": 0.2, "max_tokens": MAX_TOKENS[schema],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema, "strict": True, "schema": SCHEMAS[schema]}},
            **_params(role)}
    t0 = time.perf_counter()
    try:
        r = _http.post(f"{BASE_URL}/chat/completions", json=body, timeout=timeout,
                       headers={"Authorization": f"Bearer {KEY}"})
        r.raise_for_status()
        data = _parse(r.json()["choices"][0]["message"].get("content") or "")
        log.info("%s (%s) %.2fs", role, model, time.perf_counter() - t0)
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("%s (%s) failed after %.2fs: %s", role, model, time.perf_counter() - t0, type(e).__name__)
        return None


# ---------------------------------------------------------------- evidence helpers
def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()).strip()


def _sources(patient: Patient, raw) -> list[CandidateSource]:
    """Keep only sources whose quote is really in the documents; replace model passage IDs with the real ones."""
    out = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict) or not s.get("quote"):
            continue
        doc, pid = str(s.get("doc_id") or "") or None, str(s.get("passage_id") or "")
        if pid and pid not in patient.passage_index and doc and re.fullmatch(r"p?\d+", pid):
            pid = f"{doc}#p{pid.lstrip('p')}"                  # "p12" -> "<doc>#p12"
        if pid in patient.passage_index:
            doc = patient.passage_index[pid].doc_id
        r = verify_quote(patient, doc, str(s["quote"]))
        if r.ok:
            out.append(CandidateSource(doc_id=r.doc_id, passage_id=(r.passage_ids or [None])[0], quote=str(s["quote"])))
    return out


_ABSENT = re.compile(r"\b(not (documented|specified|recorded|mentioned|found|available|provided|given)|"
                     r"no (documentation|record|data|information|details)|unknown|"
                     r"no\b[^.;]*\b(documented|recorded|on record|found|performed))\b", re.I)


def _fact(patient: Patient, f: dict, category: str) -> CandidateFact | None:
    srcs = _sources(patient, f.get("sources"))
    if not srcs or not str(f.get("value") or "").strip() or _ABSENT.search(str(f["value"])):
        return None                                     # absence is reported via not_documented, never as a fact
    return CandidateFact(category=category, label=str(f.get("label") or category), value=str(f["value"]),
                         date=str(f.get("date") or "") or None, derived=bool(f.get("derived")), sources=srcs)


def _spans(f: CandidateFact) -> set:
    return {s.passage_id or s.quote for s in f.sources}


# ---------------------------------------------------------------- contradictions (patient-wide, cached)
def _contradictions(patient: Patient, context: str) -> list[CandidateContradiction]:
    data = _chat("contras", "contras", CONTRA_SYS, context, CONTRA_TIMEOUT_S) or {}
    out, seen = [], set()
    for c in data.get("contradictions") or []:
        if not isinstance(c, dict):
            continue
        claims, places = [], set()
        for cl in c.get("claims") or []:
            srcs = _sources(patient, [cl.get("source")]) if isinstance(cl, dict) else []
            if not srcs:
                continue
            r = verify_quote(patient, srcs[0].doc_id, srcs[0].quote)
            # a quote spanning several passages becomes one claim per passage (each passage is quoted verbatim)
            parts = ([CandidateSource(doc_id=r.doc_id, passage_id=pid, quote=patient.passage_index[pid].text)
                      for pid in r.passage_ids if pid in patient.passage_index] if len(r.passage_ids) > 1 else srcs[:1])
            for src in parts:
                if (src.passage_id or src.quote) not in places:
                    places.add(src.passage_id or src.quote)
                    claims.append(CandidateClaim(statement=str(cl.get("statement") or ""), source=src))
        key = frozenset(places)
        if len(claims) >= 2 and key not in seen:           # needs >= 2 verified, distinct passages
            seen.add(key)
            out.append(CandidateContradiction(category=str(c.get("category") or "general"),
                                              summary=str(c.get("summary") or ""), claims=claims))
    if data:
        return out
    raise RuntimeError("contradiction worker failed")


def contradictions_future(patient: Patient, context: str) -> Future:
    with _contra_lock:
        fut = _contra_cache.get(patient.patient_id)
        if fut is None or (fut.done() and fut.exception()):
            fut = _contra_cache[patient.patient_id] = _pool.submit(_contradictions, patient, context)
        return fut


def warmup(patient: Patient, context: str) -> None:
    """Start the patient-wide contradiction check in the background (call at start-up)."""
    if ENABLED:
        contradictions_future(patient, context)


# ---------------------------------------------------------------- pipeline
def _plan(role: str, task: str) -> list[dict]:
    needs = checklist_needs(role, task)
    listing = "\n".join(f"- [{n['category']}] {n['need']}" for n in needs)
    data = _chat("planner", "plan", PLAN_SYS, f"ROLE: {role}\nTASK: {task}\n\nCHECKLIST:\n{listing}", PLANNER_TIMEOUT_S)
    if data:      # checklist needs are never dropped (safety); the LLM only adds task-specific ones
        cats = {n["category"] for n in needs}
        for a in (data.get("add") or [])[:3]:
            cat = re.sub(r"[^a-z0-9_]+", "_", str(a.get("category") or "").lower()).strip("_")
            if cat and cat not in cats and a.get("need"):
                cats.add(cat)
                needs.append({"category": cat, "need": str(a["need"]), "source": "llm"})
    return [n | {"id": f"n{i + 1}"} for i, n in enumerate(needs[:12])]


def _verify(facts: list[CandidateFact], contras: list[CandidateContradiction], situation: str):
    lines = ["FACTS:"] + [f"[f{i}] {f.label}: {f.value}" + (" (calculated)" if f.derived else "") + " | quotes: "
                          + " ; ".join(f'"{s.quote}"' for s in f.sources) for i, f in enumerate(facts)]
    lines += ["", "CONTRADICTIONS:"] + [f"[c{i}] {c.summary} | " + " ; ".join(
        f'{cl.statement}: "{cl.source.quote}"' for cl in c.claims) for i, c in enumerate(contras)]
    data = _chat("verifier", "verify", VERIFY_SYS, f"SITUATION: {situation}\n\n" + "\n".join(lines), WORKER_TIMEOUT_S)
    if not data:
        return facts, contras
    bad_f = {v.get("id") for v in data.get("facts") or [] if isinstance(v, dict) and v.get("supported") is False}
    bad_c = {v.get("id") for v in data.get("contradictions") or [] if isinstance(v, dict) and v.get("real") is False}
    return ([f for i, f in enumerate(facts) if f"f{i}" not in bad_f],
            [c for i, c in enumerate(contras) if f"c{i}" not in bad_c])


def reconcile(patient: Patient, needs: list[dict], summary: dict | None, missing: dict | None):
    """Merge summary + missing-info workers: verified evidence wins, duplicates merge, off-task facts drop."""
    cats = {n["category"] for n in needs}
    by_id = {n["id"]: n["category"] for n in needs}
    facts: list[CandidateFact] = []

    def add(f: CandidateFact | None):
        if f is None or f.category not in cats:
            return
        for g in facts:     # duplicate = same category and same value or same evidence
            if g.category == f.category and (_fold(g.value) == _fold(f.value) or _spans(g) & _spans(f)):
                g.sources += [s for s in f.sources if (s.passage_id or s.quote) not in _spans(g)]
                return
        facts.append(f)

    absent: dict[str, str] = {}                        # summary facts that actually state an absence
    for f in (summary or {}).get("facts") or []:
        if isinstance(f, dict):
            cat = by_id.get(str(f.get("need_id")), "")
            if _ABSENT.search(str(f.get("value") or "")):
                absent.setdefault(cat, str(f.get("value")))
            add(_fact(patient, f, cat))
    covered = {f.category for f in facts}

    status = {by_id.get(str(m.get("need_id"))): m for m in (missing or {}).get("needs") or [] if isinstance(m, dict)}
    not_documented = []
    for n in needs:
        m = status.get(n["category"])
        if m is None and n["category"] in absent and n.get("source") == "checklist":
            m = {"documented": False, "label": absent[n["category"]]}
        if n["category"] in covered or m is None:
            continue                                   # summary found evidence, or no verdict -> say nothing
        if m.get("documented"):
            add(_fact(patient, m, n["category"]))      # fills a gap the summary worker missed (verified only)
            if any(f.category == n["category"] for f in facts):
                continue
            if _sources(patient, m.get("sources")):
                continue                               # evidence exists but no usable value: don't claim absence
        if n.get("source") != "checklist":
            continue                                   # LLM-added needs only show when found (no clutter)
        item = str(m.get("label") or "") if not m.get("documented") else ""
        not_documented.append(CandidateMissing(category=n["category"], item=item if len(item) > 8 else n["need"],
                                               searched=[str(s) for s in m.get("searched") or []][:8]))
    order = {c: i for i, c in enumerate(n["category"] for n in needs)}
    facts.sort(key=lambda f: order.get(f.category, 99))
    return facts, not_documented


def summarize(patient: Patient, role: str, task: str, context: str) -> CandidateOutput:
    t0 = time.perf_counter()
    contra_fut = contradictions_future(patient, context)          # starts now (or is cached)
    needs = _plan(role, task)
    t1 = time.perf_counter()
    listing = "\n".join(f"- {n['id']} ({n['category']}): {n['need']}" for n in needs)
    user = f"{context}\n\nROLE: {role}\nTASK: {task}\n\nINFORMATION NEEDS:\n{listing}"
    f_sum = _pool.submit(_chat, "summary", "summary", SUMMARY_SYS, user, WORKER_TIMEOUT_S)
    f_mis = _pool.submit(_chat, "missing", "missing", MISSING_SYS, user, WORKER_TIMEOUT_S)
    summary, missing = f_sum.result(), f_mis.result()
    t2 = time.perf_counter()
    try:
        remaining = max(0.5, WORKER_TIMEOUT_S - (time.perf_counter() - t0))
        contras = contra_fut.result(timeout=remaining)
    except Exception as e:  # timeout or failure: show the screen without contradictions rather than wait
        log.warning("contradictions unavailable: %s", type(e).__name__)
        contras = []
    t3 = time.perf_counter()
    facts, not_documented = reconcile(patient, needs, summary, missing)
    if VERIFY and (facts or contras):
        facts, contras = _verify(facts, contras, f"{role} {task}")
    t4 = time.perf_counter()
    last_timings.clear()
    last_timings.update(planner=round(t1 - t0, 2), workers=round(t2 - t1, 2), contradictions_wait=round(t3 - t2, 2),
                        reconcile_verify=round(t4 - t3, 2), total=round(t4 - t0, 2))
    log.info("pipeline %s", last_timings)
    return CandidateOutput(categories=[n["category"] for n in needs], facts=facts,
                           not_documented=not_documented, contradictions=contras)
