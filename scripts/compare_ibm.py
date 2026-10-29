"""Check IBM's extraction (data/patient-*/*.json) against the documents.

    python scripts/compare_ibm.py                 # prints summary, writes ibm_vs_documents.md

For every IBM entry that names its source document(s):
  CONFIRMED     its text/value is in the cited document
  OTHER_DOC     found, but only in a different document
  NOT_FOUND     not in any document  -> never show this on screen
  STATUS?       found, but IBM's status (positive/negative/suspected) disagrees
                with negation / "Vd.a." wording in the passage
Plus: medication rows and diagnosis lines in the documents that IBM does not list.
The report is a QA aid. Where IBM and the documents differ, the documents count.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import DATA_DIR  # noqa: E402
from backend.corpus import Corpus  # noqa: E402
from backend.text_norm import normalize  # noqa: E402
from backend.validate import _NEG_DE, _SUSPECT_DE  # noqa: E402
from backend.verify import verify_quote  # noqa: E402

DOC_KEYS = ("documentIds", "documentId", "documents", "docIds", "sourceDocuments")
TEXT_KEYS = ("text", "originalText", "sourceText", "name")


def _doc_ids(node: dict) -> list[str]:
    for k in DOC_KEYS:
        v = node.get(k)
        if isinstance(v, list):
            return [str(x["id"]) if isinstance(x, dict) and "id" in x else str(x) for x in v]
        if isinstance(v, (str, int)):
            return [str(v)]
    return []


def walk_entries(obj, path=(), inherited_status=None):
    """Yield (path, node, status) for every node of the values-tree that cites documents."""
    if isinstance(obj, dict):
        status = obj.get("status", inherited_status)
        name = obj.get("name")
        here = path + ((name,) if isinstance(name, str) else ())
        if _doc_ids(obj):
            yield here, obj, status
        for k, v in obj.items():
            if k in ("metadata", "code", "conceptIds"):
                continue
            if isinstance(v, (list, dict)):
                yield from walk_entries(v, here, status)
    elif isinstance(obj, list):
        for x in obj:
            yield from walk_entries(x, path, inherited_status)


def _texts(node: dict, path: tuple) -> list[str]:
    out = [node[k] for k in TEXT_KEYS if isinstance(node.get(k), str) and node[k].strip()]
    if not out and path:
        out = [path[-1]]
    return list(dict.fromkeys(out))


def _short_hit(patient, doc_id, t):
    doc = patient.documents.get(doc_id)
    if not doc:
        return None
    m = re.search(r"(?<![\wÄÖÜäöüß])" + re.escape(t.strip()) + r"(?![\wÄÖÜäöüß])", doc.text)
    return [p.id for p in doc.passages if p.start <= m.start() < p.end] if m else None


def check_text(patient, docs: list[str], texts: list[str]):
    """Best result over the candidate texts: (verdict, found_doc, passage_ids, text)."""
    best = ("NOT_FOUND", None, [], texts[0] if texts else "")
    for t in texts:
        if len(normalize(t)) < 5:
            for d in docs:
                pids = _short_hit(patient, d, t)
                if pids is not None:
                    return "CONFIRMED", d, pids, t
            for d in patient.documents:
                if d not in docs and _short_hit(patient, d, t) is not None and best[0] == "NOT_FOUND":
                    best = ("OTHER_DOC", d, [], t)
            continue
        for d in docs or [None]:
            r = verify_quote(patient, d, t)
            if r.ok and r.status != "wrong_doc":
                return "CONFIRMED", r.doc_id, r.passage_ids, t
            if r.ok and best[0] == "NOT_FOUND":
                best = ("OTHER_DOC", r.doc_id, r.passage_ids, t)
    return best


def status_conflict(patient, status: str | None, pids: list[str]) -> str | None:
    if not status or not pids:
        return None
    text = " ".join(patient.passage_index[p].text for p in pids if p in patient.passage_index)
    clauses = [c for c in re.split(r"[,;:()]|\bund\b|\bsowie\b", text) if c.strip()]
    neg = bool(_NEG_DE.search(text)) and len(clauses) == 1 and len(text.split()) <= 14
    sus = bool(_SUSPECT_DE.search(text))
    if status == "positive" and sus:
        return "IBM says positive, document says suspected (Vd.a./Verdacht)"
    if status == "positive" and neg:
        return "IBM says positive, passage contains a negation (check!)"
    if status == "negative" and not neg:
        return "IBM says negative, passage has no negation"
    if status == "suspected" and not sus:
        return "IBM says suspected, passage states it without suspicion marker"
    return None


def _value_variants(v: str, unit: str = "") -> list[str]:
    v = str(v).strip()
    out = {v, v.replace("|", "/"), v.replace("-", " - ")}
    try:                                   # IBM stores height in m, letters say "170 cm"
        f = float(v.replace(",", "."))
        if unit.strip().lower() == "m" and f < 3:
            out.add(str(round(f * 100)))
    except ValueError:
        pass
    for x in list(out):
        if "." in x:
            out.add(x.replace(".", ","))
        if "," in x:
            out.add(x.replace(",", "."))
    return [x for x in out if x]


def check_score(patient, name: str, value: str, docs: list[str], unit: str = ""):
    """Is the measured value written in the cited document? (number-boundary safe)"""
    for d in docs:
        doc = patient.documents.get(d)
        if not doc:
            continue
        for var in _value_variants(value, unit):
            for m in re.finditer(re.escape(var), doc.text):
                s, e = m.start(), m.end()
                if (s == 0 or not doc.text[s - 1].isdigit()) and (e >= len(doc.text) or not (
                        doc.text[e].isdigit() or (doc.text[e] in ".," and e + 1 < len(doc.text) and doc.text[e + 1].isdigit()))):
                    pids = [p.id for p in doc.passages if p.start <= s < p.end]
                    return "CONFIRMED", d, pids
    for doc in patient.documents.values():          # anywhere else?
        if any(var in doc.text for var in _value_variants(value, unit)):
            return "OTHER_DOC", doc.doc_id, []
    return "NOT_FOUND", None, []


def main():
    c = Corpus(DATA_DIR)
    if not c.patients:
        sys.exit(f"No patient-* folders under {DATA_DIR}. Set DATA_DIR=/path/to/data")
    lines = ["# IBM extraction vs. documents", ""]
    for pid, pat in c.patients.items():
        ex = pat.extractions
        rows, covered_texts = [], []
        counts: dict[str, int] = {}

        # 1) values-trees: diagnoses, procedures, medication (and anything shaped like them)
        for fname in sorted(ex):
            if fname in ("documents", "metadata", "patient", "scores-all", "scores-latest"):
                continue
            for path, node, status in walk_entries(ex[fname]):
                texts = _texts(node, path)
                docs = _doc_ids(node)
                verdict, found, pids, t = check_text(pat, docs, texts)
                note = status_conflict(pat, status, pids) if verdict == "CONFIRMED" else None
                if note:
                    verdict = "STATUS?"
                if verdict in ("CONFIRMED", "STATUS?", "OTHER_DOC"):
                    covered_texts.append(normalize(t))
                counts[verdict] = counts.get(verdict, 0) + 1
                rows.append((fname, verdict, " > ".join(path[-2:]), t, ",".join(docs), found or "", ",".join(pids[:2]),
                             note or status or ""))

        # 2) cave.json (adverse reactions) — usually no document ids: compare with the documents' allergy lines
        cave = ex.get("cave") or {}
        allergy_lines = [p for p in pat.passage_index.values() if re.search(r"allergi|unverträglich", p.text, re.I)
                         and p.kind != "heading"]
        for ar in cave.get("adverseReactions", []) if isinstance(cave, dict) else []:
            t = ar.get("text", "")
            hit = [p.id for p in allergy_lines if normalize(t) in normalize(p.text)]
            verdict = "CONFIRMED" if hit else "NOT_FOUND"
            counts[verdict] = counts.get(verdict, 0) + 1
            docs_say = "; ".join(sorted({" ".join(p.text.split()) for p in allergy_lines}))[:160]
            rows.append(("cave", verdict, "adverse reaction", t, "", "", ",".join(hit[:2]),
                         f"documents say: {docs_say}"))

        # 3) scores (measurements with values + documentIds)
        for fname in ("scores-all",):
            for sc in (ex.get(fname) or {}).get("scores", []):
                for v in sc.get("values", []):
                    docs = _doc_ids(v)
                    verdict, found, pids = check_score(pat, sc.get("name", ""), v.get("value", ""), docs, sc.get("unit", ""))
                    counts[verdict] = counts.get(verdict, 0) + 1
                    rows.append((fname, verdict, sc.get("name", ""), f"{v.get('value')} {sc.get('unit', '')}".strip(),
                                 ",".join(docs), found or "", ",".join(pids[:2]), (v.get("date") or {}).get("value", "")))

        # 4) what IBM does not list: medication rows and diagnosis lines in the documents
        missing = []
        for d in pat.documents.values():
            section = None
            for p in d.passages:
                if p.kind == "heading":
                    section = p.text.lower()
                    continue
                n = normalize(p.text)
                is_med = p.kind == "table_row" and re.search(r"\b(oral|s\.c\.|i\.v\.)\b", p.text)
                is_dx = section and section.startswith("diagnosen") and p.kind in ("line", "sentence", "field")
                if (is_med or is_dx) and not any(n in t or t in n for t in covered_texts if len(t) >= 3):
                    missing.append((p.id, "medication" if is_med else "diagnosis", " ".join(p.text.split())))

        # ---- output
        print(f"\nPatient {pid}: {len(rows)} IBM entries checked")
        for k in ("CONFIRMED", "OTHER_DOC", "STATUS?", "NOT_FOUND"):
            print(f"  {k:10} {counts.get(k, 0)}")
        print(f"  In documents but not in IBM: {len(missing)}")
        lines += [f"## Patient {pid}", "",
                  " | ".join(f"**{k}** {counts.get(k, 0)}" for k in ("CONFIRMED", "OTHER_DOC", "STATUS?", "NOT_FOUND")),
                  f" | **missing from IBM** {len(missing)}", "",
                  "### Problems (everything except CONFIRMED)", "",
                  "| file | verdict | where | IBM text/value | IBM doc | found in | passage | note |",
                  "|---|---|---|---|---|---|---|---|"]
        order = {"NOT_FOUND": 0, "STATUS?": 1, "OTHER_DOC": 2, "CONFIRMED": 3}
        for r in sorted(rows, key=lambda r: order[r[1]]):
            if r[1] != "CONFIRMED":
                lines.append("| " + " | ".join(str(x).replace("|", "/")[:90] for x in r) + " |")
        lines += ["", "### In the documents but not in IBM's lists", "", "| passage | kind | text |", "|---|---|---|"]
        lines += [f"| {a} | {b} | {c[:100]} |" for a, b, c in missing]
        lines += ["", "### Confirmed", "", "| file | where | IBM text/value | doc | passage |", "|---|---|---|---|---|"]
        lines += [f"| {r[0]} | {r[2][:40]} | {str(r[3])[:70]} | {r[5]} | {r[6]} |" for r in rows if r[1] == "CONFIRMED"]
        for r in sorted(rows, key=lambda r: order[r[1]])[:25]:
            if r[1] != "CONFIRMED":
                print(f"  {r[1]:9} {r[0]:12} {str(r[3])[:60]!r:64} doc={r[4] or '-'} found={r[5] or '-'}  {r[7][:70]}")
    out = Path("ibm_vs_documents.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nFull report: {out.resolve()}")


if __name__ == "__main__":
    main()
