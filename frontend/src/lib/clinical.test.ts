import { describe, it, expect } from "vitest";
import { MOCK_DOCUMENTS, mockSummary } from "./mock";
import { splitMissing, splitContradictions, categoryTitle } from "./categories";

const s = mockSummary("r", "t");

describe("clinical", () => {
  it("every mock span matches its document text", () => {
    const all = [...s.facts.flatMap((f) => f.sources), ...s.contradictions.flatMap((c) => c.claims.map((x) => x.source))];
    for (const src of all) {
      const text = MOCK_DOCUMENTS.find((d) => d.doc_id === src.doc_id)!.text;
      const parts = src.quote.split(" … ");
      src.spans.forEach(([a, b], i) => expect(text.slice(a, b)).toBe(parts[i]));
    }
  });
  it("routes not-documented items outside categories to leftover", () => {
    const r = splitMissing(s.categories, s.not_documented);
    expect(r.leftover.map((m) => m.category)).toEqual(["airway"]);
  });
  it("splits contradictions by relevance", () => {
    const r = splitContradictions(s.categories, s.contradictions);
    expect(r.relevant.map((c) => c.id)).toEqual(["c1"]);
    expect(r.other.map((c) => c.id)).toEqual(["c2"]);
  });
  it("formats category titles", () => {
    expect(categoryTitle("body_measurements")).toBe("Body measurements");
  });
});
