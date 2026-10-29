"""Benchmark LLMs on the 5 evaluation situations (cases/) with the real documents.

Pipeline per situation:  planner -> 3 parallel workers (2x facts, 1x contradictions)
                         -> validate.py (deterministic guards) -> LLM verifier
Scores: correct answers (rubric from cases/expected.json), strict citation accuracy (cited passage ID
must exist and contain the verbatim quote), unsupported claims, contradictions (gold list in
bench_out/gold_contradictions.json, git-ignored), JSON/schema validity, latency, cost.
Bench-only settings: does not touch the app's model configuration.

    DATA_DIR=... python scripts/bench_models.py --selftest              # no API calls
    DATA_DIR=... python scripts/bench_models.py --estimate MODEL...     # no token spend
    DATA_DIR=... python scripts/bench_models.py --smoke MODEL...        # tiny calls: params / JSON mode
    DATA_DIR=... python scripts/bench_models.py MODEL... [--runs 2] [--budget 3]
Key: NEBIUS_API_KEY (env or .env); base URL: LLM_BASE_URL. The key is never printed or saved.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "bench_out"
sys.path.insert(0, str(ROOT))


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_dotenv()
from pydantic import ValidationError  # noqa: E402

from backend.config import DATA_DIR  # noqa: E402
from backend.context import llm_context  # noqa: E402
from backend.corpus import Corpus  # noqa: E402
from backend.mock import mock_summarize  # noqa: E402
from backend.models import (CandidateClaim, CandidateContradiction, CandidateFact,  # noqa: E402
                            CandidateMissing, CandidateOutput, CandidateSource)
from backend.validate import validate  # noqa: E402
from backend.verify import verify_quote  # noqa: E402

KEY = os.environ.get("NEBIUS_API_KEY") or os.environ.get("LLM_API_KEY")
BASE = os.environ.get("LLM_BASE_URL", "https://api.tokenfactory.us-central1.nebius.com/v1").rstrip("/")
TEMPERATURE = 0.2
TIMEOUT_S = 90
MAX_TOKENS = {"plan": 1500, "facts": 4000, "contras": 4000, "verify": 2500}

# Fastest settings per model (bench-only). Confirm with --smoke; override with --opts '{"model": {...}}'.
MODEL_OPTS: dict[str, dict] = {
    "openai/gpt-oss-120b": {"reasoning_effort": "low"},
    "Qwen/Qwen3.5-397B-A17B": {"chat_template_kwargs": {"enable_thinking": False}},
    "deepseek-ai/DeepSeek-V4-Flash-0731": {"chat_template_kwargs": {"thinking": False}},
}

# ---------------------------------------------------------------- JSON schemas (one source of truth)
S, B = {"type": "string"}, {"type": "boolean"}


def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


SOURCE = obj({"doc_id": S, "passage_id": S, "quote": S})
SCHEMAS = {
    "plan": obj({"role": S, "task": S, "needs": arr(obj({"category": S, "need": S}))}),
    "facts": obj({"facts": arr(obj({"category": S, "label": S, "value": S, "date": S, "derived": B,
                                    "sources": arr(SOURCE)})),
                  "not_documented": arr(obj({"category": S, "item": S}))}),
    "contras": obj({"contradictions": arr(obj({"category": S, "summary": S,
                                               "claims": arr(obj({"statement": S, "source": SOURCE}))}))}),
    "verify": obj({"facts": arr(obj({"id": S, "supported": B, "reason": S})),
                   "contradictions": arr(obj({"id": S, "real": B, "reason": S}))}),
}


def schema_errors(v, s: dict, path: str = "$") -> list[str]:
    t = s["type"]
    if t == "object":
        if not isinstance(v, dict):
            return [f"{path}: not an object"]
        errs = [f"{path}.{k}: missing" for k in s["required"] if k not in v]
        for k, x in v.items():
            if k in s["properties"]:
                errs += schema_errors(x, s["properties"][k], f"{path}.{k}")
        return errs
    if t == "array":
        if not isinstance(v, list):
            return [f"{path}: not an array"]
        return [e for i, x in enumerate(v) for e in schema_errors(x, s["items"], f"{path}[{i}]")]
    ok = isinstance(v, str) if t == "string" else isinstance(v, bool)
    return [] if ok else [f"{path}: not {t}"]


# ---------------------------------------------------------------- prompts
PLAN_SYS = """You plan what a clinician needs to see about one patient, for one task.
Given the situation (role and task), list 6-10 specific information needs that this clinician needs
for this task and that could be answered from the patient's clinical documents (latest document: 15 April 2016).
Include safety-relevant needs even if they might not be documented (e.g. kidney function before contrast).
Each need: category (short snake_case), need (one specific English sentence, e.g. "Current antithrombotic drug and dose").
Return JSON only: {"role": "...", "task": "...", "needs": [{"category": "...", "need": "..."}]}"""

FACTS_SYS = """You extract facts for a clinician from the German clinical documents of one patient.
Today is 15 April 2016 (date of the latest document). Search ALL documents for each information need.
Rules:
- Use only the documents. Never add knowledge that is not in them.
- Each fact: category (of the need), label (short English), value (concise English; keep numbers, doses,
  dosing schedules like 1-0-1, dates and left/right exactly as in the source), date (ISO yyyy-mm-dd of the
  finding if known, else ""), derived (true only for values you calculated, e.g. a weight change), sources.
- Each source: doc_id and passage_id exactly as shown in the brackets, and quote = an exact, verbatim copy of
  German text from that passage (the whole passage or a contiguous part of at least 10 characters).
- Every number in value must appear in the quotes, unless derived is true.
- For current status ("does she take"), use the latest document that mentions it.
- If a need is not answered anywhere in the documents, add it to not_documented {category, item}. Do not guess.
Return JSON only: {"facts": [{"category", "label", "value", "date", "derived", "sources": [{"doc_id", "passage_id", "quote"}]}], "not_documented": [{"category", "item"}]}"""

CONTRA_SYS = """You check the German clinical documents of one patient for contradictions: two or more passages
that cannot all be true, e.g. two different dates for the same event, left vs right for the same finding,
a BMI that does not match the height and weight in the same document, or a finding stated as present in one
place and absent in another.
NOT contradictions: small measurement differences (e.g. height 170 vs 171 cm), values that changed over time
(weight, doses between hospital stays), different wording of the same fact, differences in level of detail.
Check the whole record, not only the current task. Report only contradictions you are confident about.
Each contradiction: category, summary (one English sentence naming the conflicting values), claims (2 or more),
each with statement (English) and source {doc_id, passage_id exactly as in the brackets, quote = verbatim German text from that passage}.
Return JSON only: {"contradictions": [{"category", "summary", "claims": [{"statement", "source": {"doc_id", "passage_id", "quote"}}]}]}"""

VERIFY_SYS = """You are a strict clinical fact checker. You get facts and contradictions, each with the German
source quotes it is based on.
For each fact: supported = true only if the value is fully supported by its quotes. Allowed: translation,
generic drug names, reformatting doses/schedules (e.g. "1 0 1 0" = 1-0-1), and simple calculations.
Not allowed: any detail that is not in the quotes.
For each contradiction: real = true only if the quoted passages truly cannot all be true. Small measurement
differences and values that changed over time are not real contradictions.
Return JSON only, covering every id: {"facts": [{"id", "supported", "reason"}], "contradictions": [{"id", "real", "reason"}]}"""

# ---------------------------------------------------------------- scoring rubric (from cases/expected.json)
# Each question: list of items; every item must be matched by ONE on-screen fact that cites one of the
# expected source documents. An item is a list of regexes over "label value" (ascii-folded, lowercase);
# a leading "!" means the regex must NOT match.
RUBRIC: dict[str, list[list[str]]] = {
    "1.1": [[r"\bass\b|acetylsalicyl|aspirin", r"(?<!\d)100(?!\d)"]],
    "1.2": [[r"contrast|kontrastmittel|\bkm\b"]],
    "1.3": [[r"(?<!\d)60(?!\d)", r"\bef\b|lvef|ejection|systolic|ventric"]],
    "2.1": [[r"ductal|duktal"], [r"\bt2\b", r"\bn0\b", r"\bm0\b"]],
    "2.2": [[r"oestrogen|estrogen|ostrogen|\ber\b", r"positiv|(?<!\d)100(?!\d)"], [r"progest|\bpr\b", r"positiv|(?<!\d)70(?!\d)"],
            [r"her-?2", r"negativ"], [r"ki-?67", r"(?<!\d)20(?!\d)"]],
    "2.3": [[r"quadrant"], [r"iort|intraoperativ"], [r"16\.0?5\.(20)?16|2016-05-16|16 may|may 16"]],
    "3.1": [[r"metformin|glucophage", r"(?<!\d)1000(?!\d)"], [r"pioglitazon|actos", r"(?<!\d)30(?!\d)"], [r"exenatid|byetta", r"(?<!\d)5(?!\d)"]],
    "3.2": [[r"hba1c|a1c", r"6[.,]4"]],
    "3.3": [[r"(?<!\d)78(?!\d)", r"kg|weight"], [r"(?<!\d)6[67](?!\d)", r"kg|weight"]],
    "4.1": [[r"amaurosis|vision|visual|eye|hypertensive crisis|hypertensiv"], [r"stenos|carotid|karotis|carotis"]],
    "4.2": [[r"contrast|kontrastmittel|\bkm\b"], [r"metformin|glucophage"]],
    "4.3": [[r"candesartan|blopress", r"(?<!\d)16(?!\d)", r"!1/2|\bhalf\b|0[.,]5"]],
    "5.1": [[r"duplex|doppler|ultrason|sonograph", r"month|follow|plan|recommend|control|check|schedul"],
            [r"\beye|ophthalm", r"regular|check|control|follow|recommend|exam"],
            [r"blood pressure|\bbp\b|\brr\b", r"regular|check|control|monitor|adjust|follow|recommend"]],
    "5.3": [[r"\bleft\b|links|\bli\b|sinist", r"stenos|carotid|karotis|carotis|\bica\b", r"!\bright\b|rechts"]],
}
NOT_DOC = {"5.2": r"duplex|doppler|ultrason|sonograph|carotid|karotis"}
NEGATION = r"not documented|no record|no documentation|not recorded|no evidence|not found|no follow"


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _item_ok(item: list[str], text: str) -> bool:
    return all((not re.search(p[1:], text)) if p.startswith("!") else re.search(p, text) for p in item)


def answer_ok(q: dict, facts, missing) -> bool:
    if not q["sources"]:
        topic = NOT_DOC[q["id"]]
        miss = [fold(f"{m.category} {m.item}") for m in missing]
        neg = [fold(f"{f.label} {f.value}") for f in facts]
        return any(re.search(topic, t) for t in miss) or any(re.search(topic, t) and re.search(NEGATION, t) for t in neg)
    pool = [fold(f"{f.label} {f.value}") for f in facts if any(s.doc_id in q["sources"] for s in f.sources)]
    return all(any(_item_ok(item, t) for t in pool) for item in RUBRIC[q["id"]])


def citation_status(patient, cs: CandidateSource) -> tuple[str, bool]:
    """(strict status, shown_on_screen). Strict: passage ID exists, belongs to cited doc, contains the quote."""
    eff_doc = cs.doc_id or (patient.passage_index[cs.passage_id].doc_id if cs.passage_id in patient.passage_index else None)
    shown = verify_quote(patient, eff_doc, cs.quote).ok          # what validate.py would accept
    pid = cs.passage_id
    if not pid or pid not in patient.passage_index:
        return "invented_passage_id", shown
    p = patient.passage_index[pid]
    if cs.doc_id and cs.doc_id != p.doc_id:
        return "doc_passage_mismatch", shown
    r = verify_quote(patient, p.doc_id, cs.quote)
    if not r.ok:
        return f"quote_{r.status}", shown
    if pid not in r.passage_ids:
        return "quote_not_in_cited_passage", shown
    return "ok", shown


def load_gold() -> list[dict]:
    path = OUT / "gold_contradictions.json"
    return json.loads(path.read_text()) if path.exists() else []


def classify_contradiction(c, gold: list[dict]) -> tuple[str, list[str]]:
    pids = {p for cl in c.claims for p in cl.source.passage_ids}
    hits = [g for g in gold if pids & set(g["a"]) and pids & set(g["b"])]
    good = [g["id"] for g in hits if g["kind"] in ("real", "plausible")]
    if good:
        return ("real" if any(g["kind"] == "real" for g in hits) else "plausible"), good
    if hits:
        return "false_alarm", [g["id"] for g in hits]
    return "unclassified", ["|".join(sorted(pids))]


# ---------------------------------------------------------------- data
def load_situations() -> list[dict]:
    text = (ROOT / "cases" / "situations.md").read_text()
    out = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, *rest = block.strip().splitlines()
        num, title = head.split(".", 1)
        desc = " ".join(l.strip() for l in rest if l.strip() and not re.match(r"\d+\.", l.strip()))
        out.append({"id": num.strip(), "text": f"{title.strip()}. {desc}"})
    return out


def load_expected() -> dict[str, list[dict]]:
    by_sit: dict[str, list[dict]] = {}
    for q in json.loads((ROOT / "cases" / "expected.json").read_text()):
        by_sit.setdefault(q["id"].split(".")[0], []).append(q)
    return by_sit


# ---------------------------------------------------------------- API client
def _post(path: str, body: dict | None, timeout: float):
    req = urllib.request.Request(BASE + path, method="POST" if body else "GET",
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read()), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:300]
        return e.code, {"error": msg.replace(KEY, "***") if KEY else msg}, time.perf_counter() - t0
    except Exception as e:  # timeouts, connection errors
        return 0, {"error": f"{type(e).__name__}: {e}"}, time.perf_counter() - t0


def parse_json(content: str):
    c = re.sub(r"(?s)<think>.*?</think>", "", content or "").strip()
    try:
        return json.loads(c), True
    except json.JSONDecodeError:
        pass
    c = re.sub(r"^```(?:json)?|```$", "", c.strip(), flags=re.M).strip()
    i, j = c.find("{"), c.rfind("}")
    try:
        return (json.loads(c[i:j + 1]), False) if i >= 0 < j else (None, False)
    except json.JSONDecodeError:
        return None, False


class Client:
    def __init__(self, meta: dict, budget: float):
        self.meta, self.budget, self.spent, self.lock = meta, budget, 0.0, threading.Lock()

    def cost(self, model: str, usage: dict) -> float:
        pr = self.meta.get(model, {}).get("pricing", {})
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return ((usage.get("prompt_tokens", 0) - cached) * float(pr.get("prompt", 0))
                + cached * float(pr.get("input_cache_read", pr.get("prompt", 0)))
                + usage.get("completion_tokens", 0) * float(pr.get("completion", 0)))

    def chat(self, model: str, system: str, user: str, schema: str, opts: dict, json_mode: bool = True):
        body = {"model": model, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS[schema],
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        feats = self.meta.get(model, {}).get("supported_features", [])
        mode = "prompt"
        if json_mode and "structured_outputs" in feats:
            mode, body["response_format"] = "json_schema", {
                "type": "json_schema", "json_schema": {"name": schema, "strict": True, "schema": SCHEMAS[schema]}}
        elif json_mode and "json_mode" in feats:
            mode, body["response_format"] = "json_object", {"type": "json_object"}
        body |= opts
        status, resp, dt = _post("/chat/completions", body, TIMEOUT_S)
        rec = {"schema": schema, "mode": mode, "status": status, "latency_s": round(dt, 2)}
        if status != 200:
            return None, rec | {"error": resp.get("error", "")[:300], "valid": False, "strict_json": False}
        choice = resp["choices"][0]
        msg, usage = choice["message"], resp.get("usage") or {}
        cost = self.cost(model, usage)
        with self.lock:
            self.spent += cost
        data, strict = parse_json(msg.get("content") or "")
        errs = schema_errors(data, SCHEMAS[schema]) if data is not None else ["unparseable"]
        rec |= {"finish": choice.get("finish_reason"), "in_tok": usage.get("prompt_tokens"),
                "out_tok": usage.get("completion_tokens"),
                "reasoning_tok": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cached_tok": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                "cost_usd": round(cost, 6), "strict_json": strict and not errs, "valid": not errs,
                "schema_errors": errs[:5], "raw": msg.get("content")}
        return data, rec


# ---------------------------------------------------------------- pipeline
def _src(d) -> CandidateSource:
    d = d if isinstance(d, dict) else {}
    return CandidateSource(doc_id=str(d.get("doc_id") or "") or None,
                           passage_id=str(d.get("passage_id") or "") or None, quote=str(d.get("quote") or ""))


def to_candidate(fact_outs: list, contra_out, categories: list[str]) -> CandidateOutput:
    out = CandidateOutput(categories=categories)
    for fo in fact_outs:
        fo = fo if isinstance(fo, dict) else {}
        for f in fo.get("facts") or []:
            try:
                out.facts.append(CandidateFact(
                    category=str(f.get("category") or "general"), label=str(f.get("label") or ""),
                    value=str(f.get("value") or ""), date=str(f.get("date") or "") or None,
                    derived=bool(f.get("derived")), sources=[_src(s) for s in f.get("sources") or []]))
            except (ValidationError, AttributeError, TypeError):
                pass
        for m in fo.get("not_documented") or []:
            if isinstance(m, dict):
                out.not_documented.append(CandidateMissing(category=str(m.get("category") or "general"),
                                                           item=str(m.get("item") or "")))
    for c in (contra_out or {}).get("contradictions") or [] if isinstance(contra_out, dict) else []:
        try:
            out.contradictions.append(CandidateContradiction(
                category=str(c.get("category") or "general"), summary=str(c.get("summary") or ""),
                claims=[CandidateClaim(statement=str(cl.get("statement") or ""), source=_src(cl.get("source")))
                        for cl in c.get("claims") or [] if isinstance(cl, dict)]))
        except (ValidationError, AttributeError, TypeError):
            pass
    return out


def _needs_text(needs: list[dict]) -> str:
    return "\n".join(f"- [{n.get('category', 'general')}] {n.get('need', '')}" for n in needs if isinstance(n, dict))


def _verify_payload(facts, contras) -> str:
    lines = ["FACTS:"] + [f"[{f.id}] {f.label}: {f.value}" + (" (derived)" if f.derived else "") + " | quotes: "
                          + " ; ".join(f'"{s.quote}"' for s in f.sources) for f in facts]
    lines += ["", "CONTRADICTIONS:"] + [f"[{c.id}] {c.summary} | claims: " + " ; ".join(
        f'{cl.statement} — "{cl.source.quote}"' for cl in c.claims) for c in contras]
    return "\n".join(lines)


def run_situation(client: Client, model: str, opts: dict, patient, ctx: str, sit: dict, measure_single: bool) -> dict:
    rec: dict = {"situation": sit["id"], "calls": {}}
    t0 = time.perf_counter()
    plan, c = client.chat(model, PLAN_SYS, f"SITUATION: {sit['text']}", "plan", opts)
    rec["calls"]["planner"] = c
    needs = [n for n in (plan or {}).get("needs") or [] if isinstance(n, dict)] if isinstance(plan, dict) else []
    if not needs:
        needs = [{"category": "general", "need": "All facts this clinician needs for this task"}]
    t1 = time.perf_counter()

    sit_block = f"\n\nSITUATION: {sit['text']}\n\nINFORMATION NEEDS:\n"
    jobs = [("facts", FACTS_SYS, needs[0::2] or needs), ("facts", FACTS_SYS, needs[1::2] or needs),
            ("contras", CONTRA_SYS, needs)]
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(lambda j: client.chat(model, j[1], ctx + sit_block + _needs_text(j[2]), j[0], opts), jobs))
    t2 = time.perf_counter()
    for name, (_, c) in zip(("worker_facts_1", "worker_facts_2", "worker_contras"), res):
        rec["calls"][name] = c

    cand = to_candidate([res[0][0], res[1][0]], res[2][0], [n.get("category", "general") for n in needs])
    facts, contras, missing, rejected = validate(patient, cand)
    t3 = time.perf_counter()

    dropped_f, dropped_c = set(), set()
    if facts or contras:
        ver, c = client.chat(model, VERIFY_SYS, f"SITUATION: {sit['text']}\n\n" + _verify_payload(facts, contras),
                             "verify", opts)
        rec["calls"]["verifier"] = c
        if isinstance(ver, dict):
            dropped_f = {str(v.get("id")) for v in ver.get("facts") or [] if isinstance(v, dict) and v.get("supported") is False}
            dropped_c = {str(v.get("id")) for v in ver.get("contradictions") or [] if isinstance(v, dict) and v.get("real") is False}
    t4 = time.perf_counter()

    rec["timing_s"] = {"planner": round(t1 - t0, 2), "workers_parallel": round(t2 - t1, 2),
                       "validate": round(t3 - t2, 3), "verifier": round(t4 - t3, 2), "total": round(t4 - t0, 2)}
    if measure_single:   # same worker-1 request alone, after the pipeline (no concurrency)
        _, c = client.chat(model, FACTS_SYS, ctx + sit_block + _needs_text(jobs[0][2]), "facts", opts)
        rec["calls"]["single_worker"] = c
        rec["timing_s"]["single_worker"] = c["latency_s"]
    rec["needs"] = needs
    rec["candidate"] = cand.model_dump()
    rec["pre"] = {"facts": facts, "contras": contras}
    rec["post"] = {"facts": [f for f in facts if f.id not in dropped_f],
                   "contras": [c for c in contras if c.id not in dropped_c]}
    rec["missing"], rec["rejected"] = missing, rejected
    rec["verifier_dropped"] = {"facts": sorted(dropped_f), "contras": sorted(dropped_c)}
    return rec


# ---------------------------------------------------------------- scoring
def score_runs(patient, records: list[dict], expected: dict, gold: list[dict]) -> dict:
    sc = {"answers_pre": 0, "answers_post": 0, "answer_detail": {}, "sources": 0, "src_ok": 0,
          "src_status": {}, "screen_facts": 0, "screen_facts_link_ok": 0, "facts_raw": 0,
          "rejected_facts": {}, "verifier_dropped_facts": 0, "contra": {"pre": {}, "post": {}}}
    for r in records:
        qs = expected[r["situation"]]
        for q in qs:
            pre, post = answer_ok(q, r["pre"]["facts"], r["missing"]), answer_ok(q, r["post"]["facts"], r["missing"])
            sc["answers_pre"] += pre
            sc["answers_post"] += post
            sc["answer_detail"][q["id"]] = post
        cand = CandidateOutput.model_validate(r["candidate"])
        status_by_fact = []
        for f in cand.facts:
            sts = [citation_status(patient, s) for s in f.sources]
            status_by_fact.append(sts)
        all_src = [s for f in cand.facts for s in f.sources] + [cl.source for c in cand.contradictions for cl in c.claims]
        for s in all_src:
            st, _ = citation_status(patient, s)
            sc["sources"] += 1
            sc["src_ok"] += st == "ok"
            sc["src_status"][st] = sc["src_status"].get(st, 0) + 1
        sc["facts_raw"] += len(cand.facts)
        for f in r["post"]["facts"]:
            i = int(f.id[1:])
            shown = [st for st, ok in status_by_fact[i] if ok]
            sc["screen_facts"] += 1
            sc["screen_facts_link_ok"] += bool(shown) and all(st == "ok" for st in shown)
        for rj in r["rejected"]:
            if rj.kind == "fact":
                sc["rejected_facts"][rj.reason] = sc["rejected_facts"].get(rj.reason, 0) + 1
        sc["verifier_dropped_facts"] += len(r["verifier_dropped"]["facts"])
        for stage in ("pre", "post"):
            for c in r[stage]["contras"]:
                kind, ids = classify_contradiction(c, gold)
                for gid in ids:
                    sc["contra"][stage].setdefault(kind, {}).setdefault(gid, c.summary)
    for stage in ("pre", "post"):
        k = sc["contra"][stage]
        sc[f"contra_net_{stage}"] = len(k.get("real", {})) - len(k.get("false_alarm", {})) - len(k.get("unclassified", {}))
    return sc


def _calls(records: list[dict]) -> list[tuple[str, dict]]:
    return [(name, c) for r in records for name, c in r["calls"].items()]


def summarize(model: str, runs: list[list[dict]], scores: list[dict], spent: float) -> dict:
    allrec = [r for run in runs for r in run]
    tot = [r["timing_s"]["total"] for r in allrec]
    calls = _calls(allrec)
    by_step: dict[str, list[dict]] = {}
    for name, c in calls:
        step = "worker_facts" if name.startswith("worker_facts") or name == "single_worker" else name
        by_step.setdefault(step, []).append(c)
    lat = lambda xs: (round(statistics.median(xs), 2), round(max(xs), 2)) if xs else (None, None)  # noqa: E731
    return {
        "model": model,
        "answers_post": [s["answers_post"] for s in scores], "answers_pre": [s["answers_pre"] for s in scores],
        "citation_acc": [round(s["src_ok"] / s["sources"], 3) if s["sources"] else None for s in scores],
        "src_status": [s["src_status"] for s in scores],
        "screen_link_acc": [round(s["screen_facts_link_ok"] / s["screen_facts"], 3) if s["screen_facts"] else None for s in scores],
        "screen_facts": [s["screen_facts"] for s in scores], "facts_raw": [s["facts_raw"] for s in scores],
        "rejected_facts": [s["rejected_facts"] for s in scores],
        "verifier_dropped_facts": [s["verifier_dropped_facts"] for s in scores],
        "contra_net_pre": [s["contra_net_pre"] for s in scores], "contra_net_post": [s["contra_net_post"] for s in scores],
        "contra_post": [s["contra"]["post"] for s in scores], "contra_pre": [s["contra"]["pre"] for s in scores],
        "json_valid": {k: f"{sum(c['valid'] for c in v)}/{len(v)}" for k, v in by_step.items()},
        "json_strict": {k: f"{sum(c['strict_json'] for c in v)}/{len(v)}" for k, v in by_step.items()},
        "json_mode": sorted({c["mode"] for _, c in calls}),
        "http_errors": [f"{n}: {c['status']} {c.get('error', '')[:120]}" for n, c in calls if c["status"] != 200],
        "truncated": sum(c.get("finish") == "length" for _, c in calls),
        "latency": {
            "planner_med_max": lat([r["timing_s"]["planner"] for r in allrec]),
            "single_worker_med_max": lat([r["timing_s"]["single_worker"] for r in allrec if "single_worker" in r["timing_s"]]),
            "worker_call_med_max": lat([c["latency_s"] for c in by_step.get("worker_facts", []) + by_step.get("worker_contras", [])]),
            "workers_parallel_wall_med_max": lat([r["timing_s"]["workers_parallel"] for r in allrec]),
            "verifier_med_max": lat([r["timing_s"]["verifier"] for r in allrec]),
            "pipeline_med_max": lat(tot),
            "pipeline_under_10s": f"{sum(t < 10 for t in tot)}/{len(tot)}",
        },
        "tokens": {"in": sum(c.get("in_tok") or 0 for _, c in calls), "out": sum(c.get("out_tok") or 0 for _, c in calls),
                   "reasoning": sum(c.get("reasoning_tok") or 0 for _, c in calls)},
        "cost_usd": round(spent, 4),
    }


def _dump(o):
    return o.model_dump() if hasattr(o, "model_dump") else str(o)


# ---------------------------------------------------------------- modes
def get_meta() -> dict:
    st, r, _ = _post("/models?verbose=true", None, 30)
    if st != 200:
        sys.exit(f"GET /models failed: HTTP {st} {r.get('error', '')}")
    return {m["id"]: m for m in r.get("data", [])}


def selftest(patient, sits, expected, gold) -> None:
    pi = patient.passage_index
    F = lambda sit, cat, label, value, pid, derived=False: (sit, CandidateFact(  # noqa: E731
        category=cat, label=label, value=value, derived=derived,
        sources=[CandidateSource(doc_id=pi[pid].doc_id, passage_id=pid, quote=pi[pid].text)]))
    perfect = [
        F("1", "anticoagulation", "Antithrombotic", "Thrombo ASS (acetylsalicylic acid) 100 mg 1-0-0", "12340018#p54"),
        F("1", "allergies", "Allergy", "Contrast media allergy (nausea, dizziness, vomiting)", "12340018#p18"),
        F("1", "cardiac", "LV ejection fraction", "EF 60%, normal systolic LV function", "12340012#p62"),
        F("2", "diagnosis", "Histology / TNM", "Invasive ductal carcinoma, T2 N0 M0 V0 G2", "12340016#p22"),
        F("2", "tumour_biology", "Receptor status", "ER positive 100% (Allred 8), PR positive 70% (Allred 7), HER2 negative", "12340016#p19"),
        F("2", "tumour_biology", "Ki-67", "Ki-67 20%", "12340016#p20"),
        F("2", "plan", "Tumour board plan", "Quadrantectomy + IORT on 16.05.2016", "12340018#p51"),
        F("3", "medication", "Metformin", "Metformin (Glucophage) 1000 mg 1-1-1", "12340018#p55"),
        F("3", "medication", "Pioglitazone", "Pioglitazone (Actos) 30 mg 0-1-0", "12340018#p60"),
        F("3", "medication", "Exenatide", "Exenatide (Byetta) 5 mcg s.c. 1-0-1", "12340018#p61"),
        F("3", "labs", "HbA1c", "HbA1c 6.4%", "12340011#p13"),
        F("3", "vitals", "Weight June 2015", "78 kg", "12340012#p36"),
        F("3", "vitals", "Weight April 2016", "66 kg", "12340018#p25"),
        F("4", "history", "Previous episode", "Amaurosis fugax left eye with 60% left carotid stenosis", "12340011#p18"),
        F("4", "allergies", "Allergy", "Contrast media allergy", "12340018#p18"),
        F("4", "medication", "Metformin", "Metformin (Glucophage) 1000 mg 1-1-1", "12340018#p55"),
        F("4", "medication", "Antihypertensive", "Candesartan (Blopress) 16 mg 1-0-1", "12340018#p57"),
        F("5", "follow_up", "Carotid follow-up", "Carotid duplex ultrasound planned in 2 months", "12340012#p110"),
        F("5", "follow_up", "Eye follow-up", "Regular eye check-ups recommended", "12340012#p109"),
        F("5", "follow_up", "BP follow-up", "Regular blood pressure checks, medication adjustment by GP", "12340012#p106"),
        F("5", "vascular", "Carotid stenosis side", "Left internal carotid artery stenosis, max. 60%", "12340008#p18"),
    ]
    cl = lambda pid: CandidateClaim(statement=pid, source=CandidateSource(doc_id=pi[pid].doc_id, passage_id=pid, quote=pi[pid].text))  # noqa: E731
    gold_contras = [CandidateContradiction(summary=g["id"], claims=[cl(g["a"][0]), cl(g["b"][0])]) for g in gold if g["kind"] == "real"]
    recs, mock_recs = [], []
    for s in sits:
        cand = CandidateOutput(facts=[f for sid, f in perfect if sid == s["id"]], contradictions=gold_contras if s["id"] == "1" else [])
        if s["id"] == "5":
            cand.not_documented.append(CandidateMissing(category="follow_up", item="Record of the follow-up carotid duplex ultrasound"))
        for target, c in ((recs, cand), (mock_recs, mock_summarize(patient, "", s["text"]))):
            f, k, m, rj = validate(patient, c)
            target.append({"situation": s["id"], "candidate": c.model_dump(), "pre": {"facts": f, "contras": k},
                           "post": {"facts": f, "contras": k}, "missing": m, "rejected": rj,
                           "verifier_dropped": {"facts": [], "contras": []}})
    for name, rr in (("perfect", recs), ("mock", mock_recs)):
        s = score_runs(patient, rr, expected, gold)
        print(f"{name:8} answers {s['answers_post']}/15  citations {s['src_ok']}/{s['sources']}  "
              f"screen links {s['screen_facts_link_ok']}/{s['screen_facts']}  contra net {s['contra_net_post']}  "
              f"rejected {s['rejected_facts']}  missed {[q for q, ok in s['answer_detail'].items() if not ok]}")


def estimate(models: list[str], meta: dict, ctx: str, sits: list[dict], runs: int) -> None:
    tok = lambda chars: chars / 3.0  # noqa: E731  (German + IDs; conservative)
    sit_avg = statistics.mean(len(s["text"]) for s in sits)
    needs = 700
    per_sit_in = (tok(len(PLAN_SYS) + sit_avg) + 2 * tok(len(FACTS_SYS) + len(ctx) + sit_avg + needs / 2)
                  + tok(len(CONTRA_SYS) + len(ctx) + sit_avg + needs) + tok(len(VERIFY_SYS) + 5000))
    single_in = tok(len(FACTS_SYS) + len(ctx) + sit_avg + needs / 2)
    out_lo, out_hi = 400 + 2 * 1200 + 1200 + 700, 1500 + 2 * 3500 + 4000 + 2000   # no / heavy reasoning
    calls = len(sits) * (runs * 5 + 1)
    print(f"Per situation: ~{per_sit_in:,.0f} input tokens, {out_lo:,}-{out_hi:,} output tokens; "
          f"{len(sits)} situations x {runs} runs + 1 single-worker call each = {calls} calls per model\n")
    print(f"{'model':40} {'$/M in':>7} {'$/M out':>8} {'input tok':>10} {'output tok':>16} {'cost USD':>14}")
    total_lo = total_hi = 0.0
    for m in models:
        pr = meta[m]["pricing"]
        pin, pout = float(pr["prompt"]), float(pr["completion"])
        tin = len(sits) * (runs * per_sit_in + single_in)
        tlo, thi = len(sits) * runs * out_lo + len(sits) * 1200, len(sits) * runs * out_hi + len(sits) * 3500
        lo, hi = tin * pin + tlo * pout, tin * pin + thi * pout
        total_lo, total_hi = total_lo + lo, total_hi + hi
        print(f"{m:40} {pin * 1e6:7.2f} {pout * 1e6:8.2f} {tin:10,.0f} {tlo:7,.0f}-{thi:8,.0f} {lo:6.2f}-{hi:6.2f}")
    print(f"{'TOTAL':40} {'':7} {'':8} {'':10} {'':16} {total_lo:6.2f}-{total_hi:6.2f}")


def smoke(models: list[str], meta: dict) -> None:
    variants = [{}, {"reasoning_effort": "low"}, {"chat_template_kwargs": {"enable_thinking": False}},
                {"chat_template_kwargs": {"thinking": False}}]
    spent = 0.0
    cl = Client(meta, 1.0)
    for m in models:
        print(f"\n{m}  features={meta[m].get('supported_features')}")
        for v in variants:
            body = {"model": m, "temperature": TEMPERATURE, "max_tokens": 800,
                    "messages": [{"role": "user", "content": 'What is 17*23? Answer as JSON {"answer": <int>} only.'}]} | v
            st, r, dt = _post("/chat/completions", body, 60)
            if st != 200:
                print(f"  {json.dumps(v):55} HTTP {st} {r.get('error', '')[:100]}")
                continue
            msg, u = r["choices"][0]["message"], r.get("usage") or {}
            spent += cl.cost(m, u)
            rtxt = msg.get("reasoning_content") or msg.get("reasoning") or ""
            print(f"  {json.dumps(v):55} {dt:5.2f}s out_tok={u.get('completion_tokens')} "
                  f"reasoning_tok={(u.get('completion_tokens_details') or {}).get('reasoning_tokens')} "
                  f"reasoning_chars={len(rtxt)} content={(msg.get('content') or '')[:60]!r}")
        data, rec = cl.chat(m, "Return JSON only.", 'Plan for "anaesthetist, pre-op check": role, task, 2 needs.',
                            "plan", MODEL_OPTS.get(m, {}))
        spent += rec.get("cost_usd", 0)
        print(f"  structured ({rec['mode']}, opts={MODEL_OPTS.get(m, {})}): HTTP {rec['status']} {rec['latency_s']}s "
              f"valid={rec['valid']} strict={rec['strict_json']} {rec.get('error', '')[:100]}")
    print(f"\nSmoke test cost: ${spent:.4f}")


def app_eval(patient, ctx, sits, expected, gold) -> None:
    """Score backend/ai.py end to end (what /api/summary serves). First call is cold (no contradiction cache)."""
    from backend import ai
    recs = []
    for s in sits:
        t0 = time.perf_counter()
        cand = ai.summarize(patient, "", s["text"], ctx)
        dt = time.perf_counter() - t0
        f, k, m, rj = validate(patient, cand)
        recs.append({"situation": s["id"], "candidate": cand.model_dump(), "pre": {"facts": f, "contras": k},
                     "post": {"facts": f, "contras": k}, "missing": m, "rejected": rj,
                     "verifier_dropped": {"facts": [], "contras": []}})
        sc = score_runs(patient, recs[-1:], expected, gold)
        print(f"situation {s['id']}: {dt:5.1f}s {dict(ai.last_timings)}  answers {sc['answers_post']}/{len(expected[s['id']])} "
              f"missed {[q for q, ok in sc['answer_detail'].items() if not ok]}  facts {len(f)}  "
              f"rejected {[(x.reason, x.item[:50]) for x in rj if x.kind == 'fact']}", flush=True)
    sc = score_runs(patient, recs, expected, gold)
    k = sc["contra"]["post"]
    print(f"\nTOTAL answers {sc['answers_post']}/15 | citations strict {sc['src_ok']}/{sc['sources']} "
          f"| on-screen facts with verified link {sc['screen_facts']}/{sc['screen_facts']} "
          f"| contradictions real {sorted(k.get('real', {}))} plausible {sorted(k.get('plausible', {}))} "
          f"false {sorted(k.get('false_alarm', {}))} unclassified {list(k.get('unclassified', {}).values())} "
          f"| net {sc['contra_net_post']}")
    OUT.mkdir(exist_ok=True)
    (OUT / "app_eval.json").write_text(json.dumps(recs, default=_dump, ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--opts", default="{}", help='JSON: {"model": {extra request params}}')
    ap.add_argument("--situations", default="", help="comma-separated situation ids, e.g. 1,4 (default: all)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--app", action="store_true", help="evaluate the real backend/ai.py pipeline")
    a = ap.parse_args()

    corpus = Corpus(DATA_DIR)
    if not corpus.patients:
        sys.exit(f"No patient data under {DATA_DIR}. Set DATA_DIR.")
    patient = next(iter(corpus.patients.values()))
    ctx, sits, expected, gold = llm_context(patient), load_situations(), load_expected(), load_gold()
    if a.situations and not a.selftest:
        keep = set(a.situations.split(","))
        sits = [s for s in sits if s["id"] in keep]
    if a.selftest:
        return selftest(patient, sits, expected, gold)
    if not KEY:
        sys.exit("NEBIUS_API_KEY is not set (env or .env).")
    if a.app:
        return app_eval(patient, ctx, sits, expected, gold)
    meta = get_meta()
    unknown = [m for m in a.models if m not in meta]
    if unknown:
        sys.exit(f"Not available with this key: {unknown}")
    if a.estimate:
        return estimate(a.models, meta, ctx, sits, a.runs)
    if a.smoke:
        return smoke(a.models, meta)

    opts_override = json.loads(a.opts)
    client = Client(meta, a.budget)
    OUT.mkdir(exist_ok=True)
    summaries = []
    for model in a.models:
        opts = opts_override.get(model, MODEL_OPTS.get(model, {}))
        spent0 = client.spent
        runs: list[list[dict]] = []
        print(f"\n=== {model}  opts={opts}", flush=True)
        for r in range(a.runs):
            run = []
            for s in sits:
                if client.spent > client.budget:
                    sys.exit(f"Budget ${client.budget} reached (spent ${client.spent:.3f}); stopping.")
                rec = run_situation(client, model, opts, patient, ctx, s, measure_single=(r == 0))
                run.append(rec)
                t = rec["timing_s"]
                print(f"  run {r + 1} situation {s['id']}: total {t['total']}s (plan {t['planner']}, workers "
                      f"{t['workers_parallel']}, verify {t['verifier']}) facts {len(rec['post']['facts'])} "
                      f"contras {len(rec['post']['contras'])}  spent ${client.spent:.3f}", flush=True)
            runs.append(run)
        scores = [score_runs(patient, run, expected, gold) for run in runs]
        summ = summarize(model, runs, scores, client.spent - spent0)
        summ["opts"] = opts
        summaries.append(summ)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", model)
        (OUT / f"results_{slug}.json").write_text(json.dumps(
            {"summary": summ, "scores": scores, "runs": runs}, default=_dump, ensure_ascii=False, indent=1))
        print(json.dumps(summ, ensure_ascii=False, indent=1), flush=True)
    (OUT / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=1))
    print(f"\nTotal spent: ${client.spent:.4f}. Results in {OUT}/")


if __name__ == "__main__":
    main()
