import { describe, it, expect } from "vitest";
import { cleanText, segmentDocument } from "./segment";
import { checkValue, locateQuote, verifyFact } from "./verify";
import { MOCK_DOCUMENTS, mockSummary } from "./mock";
import type { DocumentFull } from "./types";

const raw = "Diagnosen:<BR>Mammakarzinom links, ED 03. April 2014. Z. n. Quadrantektomie re.<BR>Größe: 170 cm<BR>BMI: 29.4<BR>Thrombo ASS 100mg oral 1 0 0 0 ST<BR>Die Patientin wurde mit<BR>einer Infusion behandelt.<BR>Kein Hinweis auf Metastasen<BR>V.a. Pneumonie<BR>SLE<BR>Werte &gt; 5";
const text = cleanText(raw);
const doc: DocumentFull = { doc_id: "D1", title: "T", date: "2020-01-01", type: "x", department: "y", text };
const other: DocumentFull = { ...doc, doc_id: "D2", text: "Allergie gegen Penicillin." };

describe("segmentation", () => {
  const ps = segmentDocument("D1", text);
  it("keeps the start/end invariant without surrounding whitespace", () => {
    for (const p of ps) { expect(text.slice(p.start, p.end)).toBe(p.text); expect(p.text).toBe(p.text.trim()); }
  });
  it("classifies kinds, joins wrapped prose and respects abbreviations", () => {
    const kind = (t: string) => ps.find((p) => p.text.startsWith(t))?.kind;
    expect(kind("Diagnosen:")).toBe("heading");
    expect(kind("Größe")).toBe("field");
    expect(kind("Thrombo ASS")).toBe("table_row");
    expect(ps.some((p) => p.text === "Die Patientin wurde mit\neiner Infusion behandelt.")).toBe(true);
    expect(ps.some((p) => p.text === "Mammakarzinom links, ED 03. April 2014.")).toBe(true);
    expect(ps.some((p) => p.text === "Z. n. Quadrantektomie re.")).toBe(true);
    expect(text).toContain("Werte > 5");
  });
  it("holds the invariant for the mock documents", () => {
    for (const d of MOCK_DOCUMENTS) for (const p of segmentDocument(d.doc_id, d.text)) expect(d.text.slice(p.start, p.end)).toBe(p.text);
  });
});

describe("quote verifier", () => {
  it("does not cut a number in half", () => {
    expect(locateQuote([doc], "D1", "BMI: 29")).toBeNull();
    expect(locateQuote([doc], "D1", "BMI: 29.4")?.match).toBe("exact");
  });
  it("matches normalized and case-insensitive text with exact raw offsets", () => {
    const loc = locateQuote([doc], "D1", "die  patientin wurde mit einer")!;
    expect(loc.match).toBe("case_insensitive");
    expect(text.slice(loc.start, loc.end)).toBe("Die Patientin wurde mit\neiner");
  });
  it("auto-corrects the document and rejects short partial quotes", () => {
    expect(locateQuote([doc, other], "D1", "Allergie gegen Penicillin")?.doc_id).toBe("D2");
    expect(locateQuote([doc], "D1", "SLE")).not.toBeNull();
    expect(locateQuote([doc], "D1", "Grö")).toBeNull();
    expect(locateQuote([doc], "D1", "Nicht im Text")).toBeNull();
  });
  it("rejects wrong numbers unless derived", () => {
    expect(checkValue("Height 175 cm", ["Größe: 170 cm"], false)).toMatch(/number/);
    expect(checkValue("Height 170 cm", ["Größe: 170 cm"], false)).toBeNull();
    expect(checkValue("Diabetes type 2", ["Diab. Mell. Typ II"], false)).toBeNull();
    expect(checkValue("Weight change -4 kg", ["Gewicht 70 kg"], true)).toBeNull();
  });
  it("rejects the wrong side", () => {
    expect(checkValue("Breast cancer right", ["Mammakarzinom links"], false)).toMatch(/laterality/);
    expect(checkValue("Breast cancer left", ["Mammakarzinom links"], false)).toBeNull();
  });
  it("rejects flipped negations, polarity and uncertain findings", () => {
    expect(checkValue("Metastases present", ["Kein Hinweis auf Metastasen"], false)).toMatch(/negation/);
    expect(checkValue("No metastases", ["Kein Hinweis auf Metastasen"], false)).toBeNull();
    expect(checkValue("HER2 positive", ["HER2 negativ"], false)).toMatch(/polarity/);
    expect(checkValue("Pneumonia", ["V.a. Pneumonie"], false)).toMatch(/uncertain/);
  });
  it("drops a fact with no verifiable source", () => {
    const f = mockSummary("r", "t").facts[0]!;
    expect(verifyFact(MOCK_DOCUMENTS, { ...f, sources: [{ ...f.sources[0]!, quote: "erfunden" }] }).fact).toBeNull();
    expect(verifyFact(MOCK_DOCUMENTS, f).fact).not.toBeNull();
  });
});
