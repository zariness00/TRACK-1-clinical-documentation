import os
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
os.environ["DATA_DIR"] = str(FIX)
os.environ["MOCK"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from backend.corpus import Corpus  # noqa: E402
from backend.models import CandidateOutput  # noqa: E402
from backend.validate import validate  # noqa: E402
from backend.verify import verify_quote  # noqa: E402


@pytest.fixture(scope="module")
def pat():
    return Corpus(FIX).patient("0000-0001")


def test_loads(pat):
    assert pat.name == "Anna Musterfrau"
    assert set(pat.documents) == {"brief_2015-03", "op_2015-03", "99990001"}
    assert pat.documents["brief_2015-03"].date == "2015-03-03"   # birth date skipped
    assert "medications" in pat.extractions


def test_json_ibm_format(pat):
    d = pat.documents["99990001"]                       # doc_id/title/date from metadata
    assert d.title == "Stat. Testbrief" and d.date == "2016-02-02" and d.meta()["department"] == "Testabteilung"
    assert "<BR>" not in d.text and "li > re" in d.text
    by_text = {p.text: p.kind for p in d.passages}
    assert by_text["Größe: 165 cm"] == "field" and by_text["BMI: 22"] == "field"
    assert by_text["Testamol 50mg oral 1 0 1 0 ST"] == "table_row"
    assert by_text["Nussallergie (Exanthem)"] == "line" and by_text["Allergien:"] == "heading"
    norm = [" ".join(p.text.split()) for p in d.passages]
    # wrapped prose re-joined, also across a blank line
    assert "Die Patientin berichtete seit dem Morgen ein Druckgefühl in der rechten Schulter zu haben." in norm
    assert "Die Dauer der Beschwerden wurde als 2 Stunden angegeben." in norm
    # a following field line is never swallowed
    assert "befundet: Dr. Test" in norm
    assert verify_quote(pat, "99990001", "BMI: 22").ok


def test_offsets_exact(pat):
    for d in pat.documents.values():
        for p in d.passages:
            assert d.text[p.start:p.end] == p.text
            assert p.text == p.text.strip()


def test_sentence_split_respects_abbreviations(pat):
    texts = [p.text for p in pat.documents["brief_2015-03"].passages]
    assert any(t.startswith("Sehr geehrte Frau Kollegin Dr. med. Weber") for t in texts)
    assert any("geb. 01.02.1960, die sich vom 03.03.2015" in t for t in texts)
    assert any(t.startswith("Eine Kontrolle ist in 3 Mon. vorgesehen, z. B. beim Hausarzt") for t in texts)
    assert any(t.startswith("- Mammakarzinom links, Z. n. brusterhaltender") for t in texts)


def test_table_rows(pat):
    rows = [p for p in pat.documents["brief_2015-03"].passages if p.kind == "table_row"]
    assert [r.text.split("\t")[0] for r in rows] == ["Wirkstoff", "ASS", "Ramipril"]


@pytest.mark.parametrize("quote,status", [
    ("ASS\t100 mg\t1-0-0", "exact"),
    ("ASS 100 mg 1-0-0", "normalized"),                         # tabs vs spaces
    ("in gutem Allgemeinzustand entlassen", "normalized"),      # hyphenated line break
    ('erhielt "Clexane 40 mg" s.c.', "normalized"),             # typographic quotes
    ("allergien: penicillin (exanthem)", "caseless"),
])
def test_verify_levels(pat, quote, status):
    doc = "op_2015-03" if "Clexane" in quote else "brief_2015-03"
    r = verify_quote(pat, doc, quote)
    assert r.ok and r.status == status, r
    d = pat.documents[r.doc_id]
    assert r.passage_ids and all(pid.startswith(doc) for pid in r.passage_ids)
    assert d.text[r.spans[0].start:r.spans[0].end]


def test_verify_rejects(pat):
    assert verify_quote(pat, "brief_2015-03", "Marcumar 3 mg täglich").status == "not_found"
    assert verify_quote(pat, "brief_2015-03", "ASS").status == "too_short"
    r = verify_quote(pat, "brief_2015-03", "LVEF 60 %, keine relevanten")   # cited wrong doc
    assert r.ok and r.status == "wrong_doc" and r.doc_id == "op_2015-03"
    assert verify_quote(pat, "op_2015-03.txt", "LVEF 60 %, keine relevanten").status == "exact"


def test_verify_ellipsis(pat):
    r = verify_quote(pat, "brief_2015-03", "Größe 168 cm ... BMI 31,2")
    assert r.ok and len(r.spans) == 2


def test_validator_guards(pat):
    cand = CandidateOutput.model_validate({
        "categories": ["anticoagulation", "procedures"],
        "facts": [
            {"category": "anticoagulation", "label": "Aspirin", "value": "ASS 100 mg once daily",
             "sources": [{"doc_id": "brief_2015-03", "quote": "ASS 100 mg 1-0-0"}]},
            {"category": "anticoagulation", "label": "Aspirin", "value": "ASS 300 mg",          # wrong dose
             "sources": [{"doc_id": "brief_2015-03", "quote": "ASS 100 mg 1-0-0"}]},
            {"category": "procedures", "label": "Surgery", "value": "Breast-conserving surgery, left",  # wrong side
             "sources": [{"doc_id": "op_2015-03", "quote": "Brusterhaltende Therapie rechts"}]},
            {"category": "anticoagulation", "label": "Marcumar", "value": "Phenprocoumon",       # invented
             "sources": [{"doc_id": "brief_2015-03", "quote": "Marcumar nach INR"}]},
            {"category": "vitals_body", "label": "BMI (recalculated)", "value": "24.8", "derived": True,
             "sources": [{"passage_id": None, "doc_id": "brief_2015-03", "quote": "Größe 168 cm, Gewicht 70 kg"}]},
        ],
        "contradictions": [
            {"summary": "Two dates for the breast-conserving surgery", "claims": [
                {"statement": "5 Mar 2015", "source": {"doc_id": "brief_2015-03", "quote": "brusterhaltender Therapie am 05.03.2015"}},
                {"statement": "6 Mar 2015", "source": {"doc_id": "op_2015-03", "quote": "Operation am 06.03.2015"}}]},
            {"summary": "fake", "claims": [
                {"statement": "x", "source": {"doc_id": "op_2015-03", "quote": "Operation am 06.03.2015"}},
                {"statement": "y", "source": {"doc_id": "op_2015-03", "quote": "nicht existierender Satz"}}]},
        ],
        "not_documented": [{"category": "cardiac", "item": "Pacemaker", "searched": ["Schrittmacher"]}],
    })
    facts, contras, missing, rejected = validate(pat, cand)
    assert [f.value for f in facts] == ["ASS 100 mg once daily", "24.8"]
    assert {r.reason for r in rejected} >= {"number in value not in quote", "laterality mismatch", "no verified source"}
    assert [c.summary for c in contras] == ["Two dates for the breast-conserving surgery"]
    assert len(contras[0].claims) == 2 and missing[0].item == "Pacemaker"


def test_api_end_to_end():
    from backend.api import app
    c = TestClient(app)
    assert c.get("/api/health").json()["patients"] == {"0000-0001": 3}
    assert c.get("/api/patients").json()[0]["name"] == "Anna Musterfrau"
    doc = c.get("/api/patients/0000-0001/documents/brief_2015-03").json()
    assert doc["passages"] and doc["text"]
    assert c.get("/api/patients/0000-0001/passages", params={"q": "Allergie"}).json()[0]["text"].startswith("Allergien")
    assert "[op_2015-03#p0]" in c.get("/api/patients/0000-0001/context").json()["context"]
    v = c.post("/api/verify", json={"doc_id": "brief_2015-03", "quote": "BMI 31,2 kg/m²"}).json()
    assert v["ok"] and doc["text"][v["start"]:v["end"]] == "BMI 31,2 kg/m²"

    r = c.post("/api/summary", json={"role": "Anaesthetist", "task": "pre-operative check before breast surgery"}).json()
    assert r["mock"] and "anticoagulation" in r["categories"] and "tumour_biology" not in r["categories"]
    assert r["facts"] and all(f["sources"] for f in r["facts"])
    for f in r["facts"]:   # every rendered source points at real text
        s = f["sources"][0]
        t = c.get(f"/api/patients/0000-0001/documents/{s['doc_id']}").json()["text"]
        assert t[s["start"]:s["end"]] == s["quote"]
    assert r["timings_ms"]["total"] < 1000

    r2 = c.post("/api/summary", json={"role": "Oncologist", "task": "tumour board"}).json()
    assert "tumour_biology" in r2["categories"] and r2["categories"] != r["categories"]


# ---------- guards added after the failure-case review ----------
def _facts(pat, *items):
    cand = CandidateOutput.model_validate({"facts": [
        {"category": "x", "label": v, "value": v, "sources": [{"doc_id": "99990001", "quote": q}]} for v, q in items]})
    facts, _, _, rejected = validate(pat, cand)
    return [f.value for f in facts], {r.item.split(": ", 1)[1]: r.reason for r in rejected if r.kind == "fact"}


def test_number_boundary(pat):
    r = verify_quote(pat, "99990001", "BMI: 22")         # must hit "BMI: 22", not "BMI: 22.4"
    d = pat.documents["99990001"]
    assert r.ok and d.text[r.spans[0].end] != "."
    assert not verify_quote(pat, "brief_2015-03", "Gewicht 7").ok   # "7" is half of "70"


def test_short_whole_line_quote(pat):
    assert verify_quote(pat, "99990001", "HLP").ok                 # whole line -> citable
    assert verify_quote(pat, "brief_2015-03", "HLP").status == "too_short"   # not searched elsewhere


def test_negation_polarity_certainty(pat):
    ok, rej = _facts(pat,
        ("Enlarged axillary lymph nodes", "axillär keine vergrößerten Lymphknoten"),
        ("No enlarged axillary lymph nodes", "axillär keine vergrößerten Lymphknoten"),
        ("HER2 positive", "Her2neu-Expression: negativ"),
        ("HER2 negative", "Her2neu-Expression: negativ"),
        ("Test carcinoma, right", "Vd.a. Testkarzinom re."),
        ("Suspected test carcinoma, right", "Vd.a. Testkarzinom re."),
        ("No allergies", "Nussallergie (Exanthem)"),
        ("Type 2 diabetes", "Diabetes mellitus Typ II"),
        ("Hyperlipidaemia", "HLP"),
    )
    assert ok == ["No enlarged axillary lymph nodes", "HER2 negative", "Suspected test carcinoma, right",
                  "Type 2 diabetes", "Hyperlipidaemia"]
    assert rej["Enlarged axillary lymph nodes"] == "negation mismatch"
    assert rej["HER2 positive"] == "positive/negative mismatch"
    assert rej["Test carcinoma, right"].startswith("source says suspected")
    assert rej["No allergies"] == "negation mismatch"


def test_not_documented_is_checked(pat):
    cand = CandidateOutput.model_validate({"not_documented": [
        {"category": "allergies", "item": "Allergies"},
        {"category": "lifestyle", "item": "Smoking status", "searched": ["Nikotin", "Raucher"]}]})
    _, _, kept, rejected = validate(pat, cand)
    assert [m.item for m in kept] == ["Smoking status"]
    assert any(r.kind == "missing" and r.reason.startswith("documented in") for r in rejected)
