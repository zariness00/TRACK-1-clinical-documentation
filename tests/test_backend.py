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


def test_question_is_not_a_finding(pat):
    ok, rej = _facts(pat,
        ("Test thrombosis", "Fragestellung: Testthrombose?"),
        ("Question: test thrombosis?", "Fragestellung: Testthrombose?"))
    assert ok == ["Question: test thrombosis?"]
    assert rej["Test thrombosis"].startswith("source says suspected")


# ---------- IBM extraction -> verified fact pool ----------
def test_ibm_pool(pat):
    from backend.ibm_pool import build_pool, pool_context
    pool = build_pool(pat)
    by_text = {" ".join(f.text.split()): f for f in pool.facts}
    d = pat.documents["99990001"]
    for f in pool.facts:                                    # every fact points at real text
        assert pat.documents[f.doc_id].text[f.start:f.end] == f.text and f.passage_ids
    assert by_text["Testdiagnose links"].status == "positive"
    assert by_text["Testkarzinom re."].status == "suspected" and by_text["Testkarzinom re."].status_corrected
    assert by_text["Testthrombose"].status == "question"      # "Fragestellung: Testthrombose?"
    assert by_text["vergrößerten Lymphknoten"].status == "negative"   # "... keine vergrößerten Lymphknoten."
    assert by_text["HLP"].codes == ["ICD10:T05"]
    # wrong document cited by IBM -> linked to where the text really is, with a note
    assert by_text["Art. Hypertonie"].doc_id == "99990001" and "IBM cited op_2015-03" in by_text["Art. Hypertonie"].note
    # allergy comes from the documents, not IBM's coded concept
    allergy = [f for f in pool.facts if f.kind == "allergy"]
    assert any("Nussallergie" in f.text for f in allergy) and all("Arzneimittel-Allergie" in f.note for f in allergy)
    # measurements: BMI 22 linked (not inside 22.4), height 1,65 m -> "165 cm", BMI 27 dropped
    m = {f.value: f for f in pool.facts if f.kind == "measurement"}
    assert m["22 kg/m2"].text == "BMI: 22" and m["1,65 m"].text == "Größe: 165 cm"
    dropped = {x["text"] for x in pool.dropped}
    assert {"Lungenembolie", "Body-Mass-Index: 27 kg/m2"} <= dropped
    assert pool.stats["status_corrected"] >= 2
    assert "[suspected]" in pool_context(pool)


def test_facts_endpoint():
    from backend.api import app
    c = TestClient(app)
    r = c.get("/api/patients/0000-0001/facts").json()
    assert r["stats"]["facts"] == len(r["facts"]) and r["dropped"]
    ctx = c.get("/api/patients/0000-0001/facts", params={"kind": "allergy", "as_context": True}).json()["context"]
    assert ctx.startswith("[F") and "allergy" in ctx


# ---------- rule-based contradictions ----------
def _tmp_patient(tmp_path, docs: dict):
    import json as _json
    d = tmp_path / "patient-0000-0001" / "documents"
    d.mkdir(parents=True)
    for doc_id, text in docs.items():
        (d / f"{doc_id}.json").write_text(_json.dumps({"metadata": {"id": doc_id, "title": doc_id, "date": "2016-01-01"},
                                                       "text": text}), encoding="utf-8")
    return Corpus(tmp_path).patient("0000-0001")


def _contras(p):
    from backend.contradictions import find_contradictions
    _, cs, _, rej = validate(p, CandidateOutput(contradictions=find_contradictions(p)))
    assert not [r for r in rej if r.kind == "contradiction"], rej
    return cs


def test_contradiction_rules_find_real_conflicts(tmp_path):
    p = _tmp_patient(tmp_path, {
        "A": "Aufnahme am 10/04/2016 | Entlassen am 15/04/2016<BR>Größe: 170 cm<BR>Gewicht: 78 kg<BR>BMI: 29.4<BR>"
             "Frau X wurde am 12.04.2016 in gutem Allgemeinzustand auf der Station aufgenommen.<BR>"
             "Rechts findet sich in der A. carotis interna eine Stenose zu etwa 60%.<BR>"
             "Im Tastbefund vergrößerte Lymphknoten rechts axillär.",
        "B": "Maximal 60%ige Abgangsstenose der Arteria carotis interna links.<BR>"
             "Sonographisch zeigen sich axillär keine vergrößerten Lymphknoten.",
    })
    cs = _contras(p)
    cats = sorted(c.category for c in cs)
    assert cats == ["dates", "findings", "laterality", "vitals_body"], [c.summary for c in cs]
    for c in cs:                                   # every side of every contradiction is linked
        assert len(c.claims) >= 2 and all(cl.source.passage_ids for cl in c.claims)
    assert next(c for c in cs if c.category == "findings").confidence == "medium"


def test_contradiction_rules_no_false_alarms(tmp_path):
    p = _tmp_patient(tmp_path, {
        # header "Aufnahme am X | Entlassen am Y" + matching narrative dates: consistent
        "A": "Aufnahme am 10/06/2015 | Entlassen am 14/06/2015<BR>Größe: 170 cm<BR>Gewicht: 66 kg<BR>BMI: 22,8<BR>"
             "Frau X wurde am 10.06.2015 zur Abklärung aufgenommen.<BR>"
             "Frau X konnte am 14.06.2015 in gutem Allgemeinzustand entlassen werden.<BR>"
             "Fragestellung: Ischämie? Blutung?<BR>Keine Anzeichen für eine Blutung.<BR>"
             "60% Karotisstenose links<BR>Kein Perikarderguß.",
        # weight changed over time, height differs by 1 cm: NOT contradictions
        "B": "Größe: 171 cm<BR>Gewicht: 78 kg<BR>BMI: 26,7<BR>Carotissklerose li. (60%)",
    })
    assert _contras(p) == []


def test_summary_includes_rule_contradictions_and_no_mock_fake():
    from backend.api import app
    c = TestClient(app)
    r = c.post("/api/summary", json={"role": "Anaesthetist", "task": "pre-op"}).json()
    summaries = [x["summary"] for x in r["contradictions"]]
    assert any("BMI 31,2" in s for s in summaries)                 # brief_2015-03: 168 cm / 70 kg -> 24.8
    assert not any(s.startswith("[MOCK]") for s in summaries)
    assert all(x["origin"] == "rules" for x in r["contradictions"])
    assert c.get("/api/patients/0000-0001/contradictions").json()


def test_procedure_date_rule(tmp_path):
    letter = ("Ultraschalluntersuchung der linken Brust vom 12.04.2016:<BR>Zusammenfassung:<BR>"
              "Es erfolgte die ultraschallgezielte Biopsie von 5 Stanzzylindern.<BR><BR>"
              "Histologie vom 13.04.2016:<BR>Zusammenfassung:<BR>Stanzbiopsie der linken Brust mit Formationen eines Karzinoms.<BR>"
              "Procedere:<BR>Eine weitere Stanzbiopsie ist geplant.")
    p = _tmp_patient(tmp_path, {"H": "Entnahmedatum: 11.04.2016<BR>Entnahmetechnik: Stanzbiopsie", "L": letter})
    cs = [c for c in _contras(p) if "date differs" in c.summary]
    assert len(cs) == 1 and len(cs[0].claims) == 3 and "11.04.2016" in cs[0].summary and "12.04.2016" in cs[0].summary


def test_procedure_date_rule_same_date_is_fine(tmp_path):
    p = _tmp_patient(tmp_path, {
        "H": "Entnahmedatum: 12.04.2016<BR>Entnahmetechnik: Stanzbiopsie",
        "L": "Ultraschalluntersuchung der linken Brust vom 12.04.2016:<BR>Es erfolgte die ultraschallgezielte Biopsie."})
    assert [c for c in _contras(p) if "date differs" in c.summary] == []
