"""Verified fact pool built from IBM's extraction (data/patient-*/*.json).

IBM's entries are used as CANDIDATES, never as truth ("where they and the
documents differ, the documents count"). Each entry is:
  1. linked to the exact passage that contains its text in the cited document
     (dropped if the text is not there),
  2. given a status read from the document's own wording
     (question "…?" / suspected "Vd.a., suspekt" / negated "keine …"),
     with IBM's original status kept for comparison,
  3. de-duplicated: the same passage cited by several IBM categories becomes one
     fact with all its codes/labels.
IBM's adverse reactions (cave.json) are replaced by the documents' own allergy
lines, which are more specific ("KM-Allergie (Übelkeit, …)" vs "Arzneimittel-Allergie").
Measurements (scores-all.json) are linked to the passage that contains the value.

Built once per patient at start-up; the live AI call only has to SELECT pool facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .corpus import Patient
from .text_norm import normalize
from .validate import _NEG_DE, _SUSPECT_DE
from .verify import verify_quote

SKIP_FILES = {"documents", "metadata", "patient", "scores-latest", "cave", "scores-all"}
DOC_KEYS = ("documentIds", "documentId", "documents", "docIds", "sourceDocuments")
_QUESTION = re.compile(r"\?|\bFragestellung\b", re.I)
_CLAUSES = re.compile(r"[,;:()]|\bund\b|\bsowie\b")


@dataclass
class PoolFact:
    id: str                      # "F12"
    kind: str                    # diagnosis | procedure | medication | measurement | allergy | <file name>
    label: str                   # IBM concept name, e.g. "Pioglitazon" / "Body-Mass-Index"
    text: str                    # exact document text (German) of the linked span
    doc_id: str
    doc_title: str
    doc_date: str | None
    start: int
    end: int
    passage_ids: list[str]
    status: str                  # from the DOCUMENT: positive | negative | suspected | question | statuspost | recommended | measured
    ibm_status: str | None       # what IBM said (None for allergy lines from documents)
    status_corrected: bool
    category: str = ""           # IBM tree path, e.g. "BÖSARTIGE NEUBILDUNGEN > Brustdrüse"
    codes: list[str] = field(default_factory=list)   # "ICD10:C50.9", "ATC:A10BG03"
    value: str | None = None     # measurements: "29.4 kg/m2"
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Pool:
    facts: list[PoolFact]
    dropped: list[dict]          # IBM entries not found in the documents
    stats: dict

    def to_dict(self) -> dict:
        return {"stats": self.stats, "facts": [f.to_dict() for f in self.facts], "dropped": self.dropped}


# ---------------------------------------------------------------- helpers
def _doc_ids(node: dict) -> list[str]:
    for k in DOC_KEYS:
        v = node.get(k)
        if isinstance(v, list):
            return [str(x["id"]) if isinstance(x, dict) and "id" in x else str(x) for x in v]
        if isinstance(v, (str, int)):
            return [str(v)]
    return []


def _code(node: dict) -> str | None:
    c = node.get("code")
    if isinstance(c, dict) and c.get("value"):
        return f"{c.get('catalog', '')}:{c['value']}".lstrip(":")
    return None


def _walk(obj, path=(), status=None, code=None, label=None):
    """Yield (path, node, status, code, label) for nodes that cite documents."""
    if isinstance(obj, dict):
        status = obj.get("status", status)
        code = _code(obj) or code
        name = obj.get("name")
        here = path + ((name,) if isinstance(name, str) else ())
        if obj.get("code") and isinstance(name, str):
            label = name                       # the coded concept is the most useful label
        if _doc_ids(obj):
            yield here, obj, status, code, label or (here[-1] if here else "")
        for k, v in obj.items():
            if k not in ("metadata", "code", "conceptIds") and isinstance(v, (list, dict)):
                yield from _walk(v, here, status, code, label)
    elif isinstance(obj, list):
        for x in obj:
            yield from _walk(x, path, status, code, label)


_NEG_STRICT = re.compile(r"\b(kein|keine|keinen|keiner|keinem|keines|nicht|ohne|ausgeschlossen)\b", re.I)


def _single_clause(t: str) -> bool:
    return len([c for c in _CLAUSES.split(t) if c.strip()]) == 1 and len(t.split()) <= 14


def document_status(doc, s: int, e: int, passage, ibm_status: str | None, kind: str) -> str:
    """Status as the DOCUMENT states it, judged around IBM's span doc.text[s:e].

    question   the span contains "?", is directly followed by "?", or comes AFTER
               "Fragestellung" in the same passage (text before it, e.g. the
               "Indikation: … 60% Karotisstenose links, Fragestellung: …?", is not a question)
    suspected  the span contains a suspicion marker, or one stands right before it ("Vd.a. Mamma-Ca")
    negative   a negation in a single short clause containing the span ("keine vergrößerten Lymphknoten");
               "negativ" inside a list ("Hormonrezeptor positiv, Her2neu negativ") does not negate the diagnosis
    Procedures and medication keep IBM's status: "Mammographie" was done even if the line says "Vd.a. …".
    """
    if kind not in ("diagnosis",):
        return ibm_status or "positive"
    span = doc.text[s:e]
    p_start = passage.start if passage else s
    before_in_passage = doc.text[p_start:s]
    after = doc.text[e:e + 3].lstrip()
    if "?" in span or after.startswith("?") or re.search(r"Fragestellung", before_in_passage, re.I):
        return "question"
    if _SUSPECT_DE.search(span) or re.search(r"(\bV\.?\s?a\.|\bVd\.?\s?a\.|Verdacht auf)\s*$", doc.text[max(0, s - 20):s], re.I):
        return "suspected"
    if _NEG_STRICT.search(span) and _single_clause(span):
        return "negative"
    if passage and _single_clause(passage.text) and _NEG_STRICT.search(passage.text):
        return "negative"
    if ibm_status in ("statuspost", "recommended"):
        return ibm_status
    if ibm_status == "suspected":       # IBM saw a suspicion we cannot see in the wording: keep it (safer)
        return "suspected"
    return "positive"


def _short_find(doc, t: str):
    m = re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(t.strip()) + r"(?![\wÄÖÜäöüß])", doc.text)
    return (m.start(), m.end()) if m else None


def _locate(patient: Patient, docs: list[str], text: str):
    """(doc, start, end, passage_ids) of `text` in one of the cited documents, else None."""
    for d in docs:
        doc = patient.documents.get(d)
        if not doc:
            continue
        if len(normalize(text)) < 5:
            hit = _short_find(doc, text)
            if hit:
                s, e = hit
                return doc, s, e, [p.id for p in doc.passages if p.start < e and s < p.end]
            continue
        r = verify_quote(patient, d, text)
        if r.ok and r.doc_id == d:
            return doc, r.spans[0].start, r.spans[-1].end, r.passage_ids
    return None


def _kind(fname: str) -> str:
    return {"diagnoses": "diagnosis", "procedures": "procedure", "medication": "medication"}.get(fname, fname)


def _value_variants(v: str, unit: str) -> list[str]:
    v = str(v).strip()
    out = [v, v.replace("|", "/"), v.replace("-", " - ")]
    out += [x.replace(".", ",") for x in out if "." in x] + [x.replace(",", ".") for x in out if "," in x]
    try:
        f = float(v.replace(",", "."))
        if unit.strip().lower() == "m" and f < 3:
            out.append(str(round(f * 100)))
    except ValueError:
        pass
    return list(dict.fromkeys(x for x in out if x))


def _find_value(doc, v: str, unit: str):
    for var in _value_variants(v, unit):
        for m in re.finditer(re.escape(var), doc.text):
            s, e = m.start(), m.end()
            before_ok = s == 0 or not doc.text[s - 1].isdigit()
            after_ok = e >= len(doc.text) or not (doc.text[e].isdigit() or (
                doc.text[e] in ".," and e + 1 < len(doc.text) and doc.text[e + 1].isdigit()))
            if before_ok and after_ok:
                return s, e
    return None


# ---------------------------------------------------------------- build
def build_pool(patient: Patient) -> Pool:
    ex = patient.extractions
    by_span: dict[tuple[str, int, int], PoolFact] = {}
    dropped: list[dict] = []
    n_entries = 0

    def add(kind, label, doc, s, e, pids, status, ibm_status, category="", code=None, value=None, note=None):
        key = (doc.doc_id, s, e, kind)
        if key in by_span:
            f = by_span[key]
            if code and code not in f.codes:
                f.codes.append(code)
            if label and label not in f.label.split(" / "):
                f.label = f"{f.label} / {label}"
            return
        by_span[key] = PoolFact(
            id="", kind=kind, label=label, text=doc.text[s:e], doc_id=doc.doc_id, doc_title=doc.title,
            doc_date=doc.date, start=s, end=e, passage_ids=pids, status=status, ibm_status=ibm_status,
            status_corrected=(ibm_status is not None and status != ibm_status and status != "measured"),
            category=category, codes=[code] if code else [], value=value, note=note)

    # 1) values-trees: diagnoses, procedures, medication (and any other file of that shape)
    for fname in sorted(ex):
        if fname in SKIP_FILES:
            continue
        for path, node, ibm_status, code, label in _walk(ex[fname]):
            n_entries += 1
            texts = [node[k] for k in ("text", "name") if isinstance(node.get(k), str) and node[k].strip()]
            docs = _doc_ids(node)
            loc = next((x for t in dict.fromkeys(texts) if (x := _locate(patient, docs, t))), None)
            note = None
            if not loc:   # IBM cited the wrong document? link to where the text really is (long texts only)
                others = [d for d in patient.documents if d not in docs]
                loc = next((x for t in dict.fromkeys(texts) if len(normalize(t)) >= 12
                            and (x := _locate(patient, others, t))), None)
                if loc:
                    note = f"IBM cited {', '.join(docs)}; text found in {loc[0].doc_id}"
            if not loc:
                dropped.append({"file": fname, "text": texts[0] if texts else "", "documents": docs,
                                "ibm_status": ibm_status, "reason": "text not found in cited document"})
                continue
            doc, s, e, pids = loc
            passage = patient.passage_index.get(pids[0]) if pids else None
            status = document_status(doc, s, e, passage, ibm_status, _kind(fname))
            add(_kind(fname), label, doc, s, e, pids, status, ibm_status,
                category=" > ".join(path[:2]), code=code, note=note)

    # 2) allergies: use the documents' own lines (more specific than IBM's coded concept)
    cave = ex.get("cave") if isinstance(ex.get("cave"), dict) else {}
    ibm_allergy = ", ".join(a.get("text", "") for a in cave.get("adverseReactions", []) if isinstance(a, dict))
    n_entries += len(cave.get("adverseReactions", []))
    for doc in patient.documents.values():
        after_allergy_heading = False
        for p in doc.passages:
            if p.kind == "heading":
                after_allergy_heading = p.text.lower().startswith("allergi")
                continue
            mentions = bool(re.search(r"allergi|unverträglich", p.text, re.I))
            if (mentions or after_allergy_heading) and not (p.kind == "sentence" and len(p.text) > 160):
                # stand-alone allergy statements only, not long narrative sentences that mention it
                st = "negative" if re.search(r"\bkeine\b.*(allergi|bekannt)|\bnicht bekannt\b", p.text, re.I) else "positive"
                add("allergy", "Allergy", doc, p.start, p.end, [p.id], st, None,
                    note=(f"IBM coded this as: {ibm_allergy}" if ibm_allergy else None))
            after_allergy_heading = False

    # 3) measurements
    for sc in (ex.get("scores-all") or {}).get("scores", []):
        unit = sc.get("unit", "") or ""
        for v in sc.get("values", []):
            n_entries += 1
            docs, val = _doc_ids(v), str(v.get("value", ""))
            hit = None
            for d in docs:
                doc = patient.documents.get(d)
                if doc and (se := _find_value(doc, val, unit)):
                    hit = (doc, *se)
                    break
            if not hit:
                dropped.append({"file": "scores-all", "text": f"{sc.get('name')}: {val} {unit}".strip(),
                                "documents": docs, "ibm_status": None, "reason": "value not found in cited document"})
                continue
            doc, s, e = hit
            pids = [p.id for p in doc.passages if p.start <= s < p.end]
            p0 = patient.passage_index.get(pids[0]) if pids else None
            ps, pe = (p0.start, p0.end) if p0 else (s, e)           # link the whole line, e.g. "BMI: 29.4"
            add("measurement", sc.get("name", ""), doc, ps, pe, pids, "measured", None,
                value=f"{val.replace('|', '/')} {unit}".strip())

    facts = sorted(by_span.values(), key=lambda f: (f.doc_date or "", f.doc_id, f.start))
    for i, f in enumerate(facts):
        f.id = f"F{i}"
    stats = {"ibm_entries": n_entries, "facts": len(facts), "dropped": len(dropped),
             "status_corrected": sum(f.status_corrected for f in facts),
             "by_kind": {k: sum(f.kind == k for f in facts) for k in sorted({f.kind for f in facts})}}
    return Pool(facts, dropped, stats)


def pool_context(pool: Pool) -> str:
    """Compact prompt text: one line per fact. The model selects fact IDs."""
    lines = []
    for f in pool.facts:
        st = "" if f.status in ("positive", "measured") else f" [{f.status}]"
        val = f" = {f.value}" if f.value else ""
        lines.append(f"[{f.id}] {f.kind}: {f.label}{val}{st} — \"{' '.join(f.text.split())}\" ({f.doc_id}, {f.doc_date})")
    return "\n".join(lines)
