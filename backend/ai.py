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
from .validate import _ints, certainty_ok, laterality_ok, negation_ok, numbers_ok, polarity_ok
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
PLANNER_TIMEOUT_S = float(os.environ.get("LLM_PLANNER_TIMEOUT_S", "2"))   # only for unfamiliar roles/tasks
BUDGET_S = float(os.environ.get("LLM_BUDGET_S", "9"))          # hard end-to-end budget for one request
WORKER_TIMEOUT_S = BUDGET_S - 0.5
CONTRA_TIMEOUT_S = float(os.environ.get("LLM_CONTRA_TIMEOUT_S", "90"))
MAX_TOKENS = {"plan": 600, "summary": 3000, "missing": 2500, "contras": 8000, "verify": 1500}
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
LIST_NEEDS = {"current_medication", "diagnoses", "comorbidities"}     # broad lists: one concise fact, asked last


def checklist_needs(role: str, task: str) -> tuple[list[dict], bool]:
    """(needs, known): known = the role/task matched at least one checklist."""
    q = f" {role} {task} ".lower()
    seen, needs = set(), []
    for keys, items in CHECKLISTS:
        if any(k in q for k in keys):
            for cat, need in items:
                if cat not in seen:
                    seen.add(cat)
                    needs.append({"category": cat, "need": need, "source": "checklist"})
    if needs:
        return needs, True
    return [{"category": c, "need": n, "source": "checklist"} for c, n in DEFAULT_NEEDS], False


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
UNKNOWN_HINT = ("\nThis role/task has no specific checklist: the checklist above is generic, so add up to 6 needs "
                "that this clinician needs for this task (most important first).")

SUMMARY_SYS = f"""You extract facts for a clinician from the German clinical documents of one patient.
Today is {TODAY} (date of the latest document). Search ALL documents for each information need.
- The needs are listed by priority for this clinician's task (most important first). Output facts in that
  order: finish the first needs before moving on, so the most important facts come first.
- Use only the documents. Never add knowledge that is not in them. Skip needs you cannot answer.
- One fact per distinct item: one per drug, one per measurement or finding. need_id = the id of the need it
  answers (e.g. "n3"). A drug that answers a specific need (e.g. the antithrombotic) gets its own fact for that need.
- Needs marked [LIST] (e.g. all current medication, all diagnoses): ONE concise fact listing every item, with
  one source per item. Never drop an item to shorten the list.
- label: short English; value: concise English (translate German terms; brand name plus generic name for drugs),
  keep numbers, doses, schedules (1-0-1) and left/right exactly.
- Only facts that are present. Never output a fact saying something is "not documented" / "not specified".
- Do NOT put dates in value (the date field and the source document show them), unless the date is the answer
  itself (e.g. date of a planned operation) and is in the quote.
- date: ISO yyyy-mm-dd of the finding if known, else "". derived: true only for values you calculated (e.g. weight change).
- Every number in value must appear in the quotes unless derived is true. Write left/right only if the quote
  says it (links/rechts/li./re.). Split compound answers into separate facts that are each fully supported by
  their own quote (e.g. "previous episode: ..." and "cause found: ..." as two facts).
- For current status ("does she take"), use the latest document that mentions it and give its date.
- Be brief: at most 3 facts per need ([LIST]: one), one source per fact unless the value combines passages, quote only the
  relevant words (about 10-120 characters), not whole paragraphs.
- {CITE}
Return JSON only: {{"facts": [{{"need_id", "label", "value", "date", "derived", "sources": [{{"doc_id", "passage_id", "quote"}}]}}]}}"""

MISSING_SYS = f"""You check, for each information need, whether it is documented ANYWHERE in the German clinical
documents of one patient (latest document {TODAY}). Search all documents, including tables, headings and abbreviations
(e.g. KM-Allergie, ASS, LVEF/EF, Krea/GFR).
For every need return: need_id (e.g. "n3"), documented (true/false), searched (at most 3 German terms you checked),
and if documented: label, value (short English answer, no dates, every number must be in the quote) and ONE source
with the evidence; if not documented: label = exactly what is missing, specific (e.g. "No record that the carotid
duplex follow-up took place"), value = "", sources = []. Only mark documented when you can quote the evidence; the
value must be fully supported by that one quote. If a need covers several items (e.g. each recommended follow-up),
return one entry per item with the same need_id. Go through the needs in the listed order (most important
first). For [LIST] needs, one entry is enough. Keep it short.
- {CITE}
Return JSON only: {{"needs": [{{"need_id", "documented", "searched", "label", "value", "sources": [{{"doc_id", "passage_id", "quote"}}]}}]}}"""

CONTRA_SYS = f"""You check the German clinical documents of one patient for genuine contradictions: two or more passages
that cannot all be true. Work through these checks systematically, across ALL documents:
1. Dates: for every event (admission, discharge, examination, biopsy/sampling, surgery, report), compare every date
   given for that SAME event, within a document and across documents (e.g. "Aufnahme am" vs "... aufgenommen";
   a sampling date vs the date of the examination in which the sample was taken). Also impossible dates.
2. Findings: the same finding stated as present in one passage and absent ("kein", "keine", "ohne") in another,
   when both refer to the same time period / work-up.
3. Laterality: the same finding (same organ, same degree) recorded as left in one passage and right in another.
4. Calculated values: a stated value that does not match its components in the same document (e.g. BMI vs height
   and weight, totals).
5. Identifiers: a case number or ID that belongs to a different stay.
NOT contradictions: small measurement differences (e.g. 170 vs 171 cm), values that changed over time (weight, BMI or
doses between visits or stays), a plan vs what happened later, different wording or level of detail, questions
("Fragestellung: ...?"). Check the dates of both passages before calling something a contradiction.
Report only contradictions you are confident about.
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


def _salvage(c: str):
    """Recover a truncated JSON object: cut after the last complete element and close open brackets."""
    for end in [m.end() for m in re.finditer(r"[}\]]", c)][::-1][:200]:
        stack, in_str, esc = [], False, False
        for ch in c[:end]:
            if in_str:
                esc, in_str = (not esc and ch == "\\"), in_str and not (ch == '"' and not esc)
            elif ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        if not in_str:
            try:
                return json.loads(c[:end] + "".join(reversed(stack)), strict=False)
            except json.JSONDecodeError:
                continue
    return None


def _parse(content: str):
    c = re.sub(r"(?s)<think>.*?</think>", "", content or "").strip()
    c = re.sub(r"^```(?:json)?\s*|\s*```$", "", c)
    i, j = c.find("{"), c.rfind("}")
    for cand in (c, c[i:j + 1] if 0 <= i < j else ""):
        try:
            return json.loads(cand, strict=False)      # strict=False: raw tabs/newlines from table quotes
        except json.JSONDecodeError:
            pass
    return _salvage(c[i:]) if i >= 0 else None


SHARED_SYS = ("You are a clinical documentation assistant. You read the German clinical documents of one patient "
              "and answer strictly from them, in JSON.")


def _chat(role: str, schema: str, system: str, user: str, timeout: float, docs: str | None = None):
    """With `docs`, every worker sends the same prefix (shared system prompt + documents) so the provider's
    prompt cache can reuse it; the worker-specific instructions follow the documents."""
    model = MODELS[role]
    messages = ([{"role": "system", "content": SHARED_SYS},
                 {"role": "user", "content": f"{docs}\n\n=== INSTRUCTIONS ===\n{system}\n\n{user}"}] if docs else
                [{"role": "system", "content": system}, {"role": "user", "content": user}])
    body = {"model": model, "temperature": 0.2, "max_tokens": MAX_TOKENS[schema], "messages": messages,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema, "strict": True, "schema": SCHEMAS[schema]}},
            **_params(role)}
    # Streamed: at the deadline we stop reading and keep every complete element received so far
    # (a slow worker degrades to a partial answer instead of an empty one).
    t0, parts, complete = time.perf_counter(), [], False
    deadline = t0 + timeout
    try:
        with _http.stream("POST", f"{BASE_URL}/chat/completions", json=body | {"stream": True},
                          timeout=httpx.Timeout(timeout, connect=min(3.0, timeout)),  # fail fast on bad network
                          headers={"Authorization": f"Bearer {KEY}"}) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        complete = True
                        break
                    choices = json.loads(payload).get("choices") or [{}]
                    parts.append((choices[0].get("delta") or {}).get("content") or "")
                if time.perf_counter() > deadline:
                    break
    except (httpx.HTTPError, ValueError) as e:
        if not parts:
            log.warning("%s (%s) failed after %.2fs: %s", role, model, time.perf_counter() - t0, type(e).__name__)
            return None
    data = _parse("".join(parts))
    log.log(logging.INFO if complete else logging.WARNING, "%s (%s) %.2fs%s", role, model,
            time.perf_counter() - t0, "" if complete else " (deadline: partial output kept)")
    if not isinstance(data, dict):
        return None
    if not complete:
        data["_partial"] = True
    return data


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
                     r"no\b[^.;]*\b(documented|documentation|record|recorded|on record|found|performed))\b", re.I)


def _guards_ok(value: str, derived: bool, quotes: list[str]) -> bool:
    """Same guards validate.py applies later, so a fact that would be rejected doesn't block a better one."""
    return ((derived or numbers_ok(value, quotes)) and laterality_ok(value, quotes) and polarity_ok(value, quotes)
            and negation_ok(value, quotes) and certainty_ok(value, quotes))


_DATE_BITS = re.compile(r"\s*\(\s*\d{4}-\d{2}(-\d{2})?\s*\)|\s*\b(on|from|at)?\s*\d{4}-\d{2}-\d{2}\b")
_EXTRAS = re.compile(r"\s*\([^()]*\)|\s*\b(?:in|on|from|since|at)?\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"
                     r"|\s*\b(?:in|on|from|since|at)?\s*\d{1,2}/\d{4}\b")


# words that change the clinical meaning of a value: such values are never trimmed (all-or-nothing)
_QUALIFIER = re.compile(r"\b(paus\w*|stop\w*|discontinu\w*|until|till|bis|ended|ceased|held|withheld|abgesetzt|"
                        r"pausiert|increas\w*|decreas\w*|reduc\w*|chang\w*|switch\w*|replac\w*|erhöht|reduziert|"
                        r"umgestellt|not|no|kein\w*|without|ohne|instead)\b", re.I)
_UNIT = r"(mg|µg|ug|mcg|g|ml|ie|iu|%|kg|cm|mm|mmhg|mmol|/min)"
_NUM_UNIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*" + _UNIT + r"(?![a-z])", re.I)
_ANY_DATE = re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|\b\d{1,2}/\d{4}\b|\d{4}-\d{2}(-\d{2})?")
_SIDE = re.compile(r"\b(left|right|links|rechts|li|re|sin|dex|bilateral|beidseits|bds)\b\.?", re.I)
_ANNOTATION_WORDS = re.compile(r"\b(in|on|from|since|at|am|vom|dated|left|right|links|rechts|li|re|sin|dex|"
                               r"bilateral|mg|µg|ug|mcg|g|ml|ie|iu|kg|cm|mm|mmhg|mmol|min)\b", re.I)


def _conflicts(text: str, quotes: list[str]) -> bool:
    """True if `text` states a dose/measurement, side or date that the quotes state DIFFERENTLY
    (as opposed to the quotes simply not mentioning it)."""
    q = " ".join(quotes)
    have = set().union(*(_ints(x) for x in quotes))
    for num, unit in _NUM_UNIT.findall(text):
        if (num.split(",")[0].split(".")[0].lstrip("0") or "0") not in have and \
                re.search(r"\d\s*" + re.escape(unit), q, re.I):          # also German forms like "60%ige"
            return True
    for part in re.split(r";\s*", text):                                 # per part: "left; right" must not cancel
        if _SIDE.search(part) and _SIDE.search(q) and not laterality_ok(part, quotes):
            return True
    return bool(_ANY_DATE.search(text) and _ANY_DATE.search(q) and _ints(text) - have)


def _supported_part(value: str, derived: bool, quotes: list[str]) -> str | None:
    """Keep what the quotes support. Models add context the cited quote doesn't contain ("in 06/2015",
    "(60% left)", ISO dates). Drop ONLY such pure annotations (numbers/units/dates/side, no other words) and only
    when the quotes don't state that kind of value at all; then keep the ";"-separated parts that pass every
    guard. Never trimmed (whole fact rejected instead): values with status/change words (paused, until, reduced,
    no ...), and anything that conflicts with the quotes (other dose, other side, other date)."""
    if _guards_ok(value, derived, quotes):
        return value
    if _QUALIFIER.search(value) or _conflicts(value, quotes):
        return None
    have = set().union(*(_ints(q) for q in quotes))

    def drop(m):
        t = m.group()
        unsupported = _ints(t) - have or not laterality_ok(t, quotes)
        pure = not re.search(r"[a-zäöüß]", _ANNOTATION_WORDS.sub("", t), re.I)
        return "" if unsupported and pure else t

    v = _EXTRAS.sub(drop, _DATE_BITS.sub("", value))
    v = re.sub(r"\s{2,}", " ", v).strip(" ,;")
    if v and _guards_ok(v, derived, quotes):
        return v
    parts = [p.strip() for p in re.split(r";\s*", v) if p.strip()]
    kept = [p for p in parts if _guards_ok(p, derived, quotes)]
    return "; ".join(kept) or None


def _fact(patient: Patient, f: dict, category: str) -> CandidateFact | None:
    srcs = _sources(patient, f.get("sources"))
    value = str(f.get("value") or "").strip()
    if not srcs or not value or _ABSENT.search(value):
        return None                                     # absence is reported via not_documented, never as a fact
    quotes = [" … ".join(s.text for s in verify_quote(patient, s.doc_id, s.quote).spans) for s in srcs]
    value = _supported_part(value, bool(f.get("derived")), quotes)
    if not value:
        return None
    return CandidateFact(category=category, label=str(f.get("label") or category), value=value,
                         date=str(f.get("date") or "") or None, derived=bool(f.get("derived")), sources=srcs)


def _spans(f: CandidateFact) -> set:
    return {s.passage_id or s.quote for s in f.sources}


# ---------------------------------------------------------------- contradictions (patient-wide, cached)
def _contradictions(patient: Patient, context: str) -> list[CandidateContradiction]:
    data = _chat("contras", "contras", CONTRA_SYS, "", CONTRA_TIMEOUT_S, docs=context) or {}
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
                                              summary=str(c.get("summary") or ""), claims=claims, origin="ai"))
    if not data:
        raise RuntimeError("contradiction worker failed")
    if out:     # semantic check runs once per patient (cached), so it costs no request time
        _, out = _verify([], out, "Patient-wide contradiction check", timeout=CONTRA_TIMEOUT_S)
    return out


def contradictions_future(patient: Patient, context: str) -> Future:
    with _contra_lock:
        fut = _contra_cache.get(patient.patient_id)
        if fut is None or (fut.done() and fut.exception()):
            failed_before = fut is not None
            fut = _contra_cache[patient.patient_id] = _pool.submit(_contradictions, patient, context)
            fut.no_wait = failed_before         # a retry runs in the background; requests don't wait for it
        return fut


def warmup(patient: Patient, context: str, wait: bool = True) -> bool:
    """Run the patient-wide contradiction check at start-up. With wait=True the server only starts serving
    once it is done (bounded by LLM_CONTRA_TIMEOUT_S). Returns True if the result is ready."""
    if not ENABLED:
        return False
    fut = contradictions_future(patient, context)
    if wait:
        try:
            fut.result(timeout=CONTRA_TIMEOUT_S)
        except Exception as e:  # requests still work: rule-based contradictions + retry on next request
            log.warning("contradiction warm-up failed: %s", type(e).__name__)
            fut.no_wait = True                  # still running or failed: never block requests on it
    return fut.done() and not fut.exception()


def contradictions_status(patient_id: str) -> str:
    fut = _contra_cache.get(patient_id)
    return "not started" if fut is None else "loading" if not fut.done() else "failed" if fut.exception() else "ready"


# ---------------------------------------------------------------- pipeline
def _plan(role: str, task: str) -> list[dict]:
    """Known role/task -> its checklist, no LLM call (the workers still get the full task text).
    Unfamiliar role/task -> LLM plans task-specific needs on top of a generic checklist (short timeout,
    falls back to the generic checklist)."""
    needs, known = checklist_needs(role, task)
    if not known:
        listing = "\n".join(f"- [{n['category']}] {n['need']}" for n in needs)
        data = _chat("planner", "plan", PLAN_SYS + UNKNOWN_HINT,
                     f"ROLE: {role}\nTASK: {task}\n\nCHECKLIST:\n{listing}", PLANNER_TIMEOUT_S)
        cats = {n["category"] for n in needs}
        for a in ((data or {}).get("add") or [])[:6]:
            cat = re.sub(r"[^a-z0-9_]+", "_", str(a.get("category") or "").lower()).strip("_")
            if cat and cat not in cats and a.get("need"):
                cats.add(cat)
                needs.append({"category": cat, "need": str(a["need"]), "source": "planner"})
    # priority order: task-specific needs first (checklist / planner order), broad list needs last
    needs.sort(key=lambda n: n["category"] in LIST_NEEDS)
    return [n | {"id": f"n{i + 1}"} for i, n in enumerate(needs[:12])]


def _verify(facts: list[CandidateFact], contras: list[CandidateContradiction], situation: str,
            timeout: float = WORKER_TIMEOUT_S):
    lines = ["FACTS:"] + [f"[f{i}] {f.label}: {f.value}" + (" (calculated)" if f.derived else "") + " | quotes: "
                          + " ; ".join(f'"{s.quote}"' for s in f.sources) for i, f in enumerate(facts)]
    lines += ["", "CONTRADICTIONS:"] + [f"[c{i}] {c.summary} | " + " ; ".join(
        f'{cl.statement}: "{cl.source.quote}"' for cl in c.claims) for i, c in enumerate(contras)]
    data = _chat("verifier", "verify", VERIFY_SYS, f"SITUATION: {situation}\n\n" + "\n".join(lines), timeout)
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

    status: dict[str, list[dict]] = {}                 # missing-info worker: one or more entries per need
    for m in (missing or {}).get("needs") or []:
        if isinstance(m, dict):
            status.setdefault(by_id.get(str(m.get("need_id")), ""), []).append(m)
    not_documented = []
    for n in needs:
        cat = n["category"]
        entries = status.get(cat) or ([{"documented": False, "label": absent[cat]}] if cat in absent else [])
        for m in entries:
            if m.get("documented"):
                add(_fact(patient, m, cat))            # fills gaps the summary worker missed (verified only)
        has_fact = any(f.category == cat for f in facts)
        if n.get("source") not in ("checklist", "planner"):
            continue                                   # optional extra needs only show when found (no clutter)
        if not has_fact and any(m.get("documented") and _sources(patient, m.get("sources")) for m in entries):
            continue                                   # evidence exists but no usable value: don't claim absence
        for m in entries:
            if m.get("documented"):
                continue
            item = str(m.get("label") or "").strip()
            specific = len(item) > 8 and _fold(item) != _fold(n["need"])
            if has_fact and not specific:
                continue                               # the need is answered; only list specific missing items
            not_documented.append(CandidateMissing(category=cat, item=item if specific else n["need"],
                                                   searched=[str(s) for s in m.get("searched") or []][:8]))
    order = {c: i for i, c in enumerate(n["category"] for n in needs)}
    facts.sort(key=lambda f: order.get(f.category, 99))
    # needs with neither a verified fact nor a "not documented" verdict were not fully analysed (a worker hit the
    # time limit or failed): say so explicitly instead of implying that nothing is missing
    answered = {f.category for f in facts} | {m.category for m in not_documented}
    not_checked = [n["need"] for n in needs if n.get("source") in ("checklist", "planner")
                   and n["category"] not in answered]
    return facts, not_documented, not_checked


def summarize(patient: Patient, role: str, task: str, context: str) -> CandidateOutput:
    t0 = time.perf_counter()
    contra_fut = contradictions_future(patient, context)          # starts now (or is cached)
    needs = _plan(role, task)
    t1 = time.perf_counter()
    listing = "\n".join(f"- {n['id']} ({n['category']}){' [LIST]' if n['category'] in LIST_NEEDS else ''}: "
                        f"{n['need']}" for n in needs)
    user = f"ROLE: {role}\nTASK: {task}\n\nINFORMATION NEEDS:\n{listing}"
    worker_timeout = max(2.0, BUDGET_S - 0.3 - (t1 - t0))      # whatever the planner left of the budget
    f_sum = _pool.submit(_chat, "summary", "summary", SUMMARY_SYS, user, worker_timeout, context)
    f_mis = _pool.submit(_chat, "missing", "missing", MISSING_SYS, user, worker_timeout, context)
    summary, missing = f_sum.result(), f_mis.result()       # if one fails, reconcile() uses the other
    t2 = time.perf_counter()
    try:
        # wait only for a first computation still in progress; after a failure the retry runs in the background
        remaining = 0 if getattr(contra_fut, "no_wait", False) else max(0.1, BUDGET_S - 0.2 - (time.perf_counter() - t0))
        contras = contra_fut.result(timeout=remaining)
    except Exception as e:  # still loading or failed: serve the screen (rule-based contradictions still shown)
        log.warning("AI contradictions %s: %s", contradictions_status(patient.patient_id), type(e).__name__)
        contras = []
    t3 = time.perf_counter()
    facts, not_documented, not_checked = reconcile(patient, needs, summary, missing)
    incomplete = [n for n, d in (("summary", summary), ("missing-info", missing)) if d is None or d.get("_partial")]
    if not_checked:
        log.warning("analysis incomplete for %d need(s); workers cut off/failed: %s", len(not_checked), incomplete)
    if VERIFY and (facts or contras):
        facts, contras = _verify(facts, contras, f"{role} {task}")
    t4 = time.perf_counter()
    last_timings.clear()
    last_timings.update(planner=round(t1 - t0, 2), workers=round(t2 - t1, 2), contradictions_wait=round(t3 - t2, 2),
                        reconcile_verify=round(t4 - t3, 2), total=round(t4 - t0, 2))
    log.info("pipeline %s", last_timings)
    return CandidateOutput(categories=[n["category"] for n in needs], facts=facts,
                           not_documented=not_documented, contradictions=contras, not_checked=not_checked)
