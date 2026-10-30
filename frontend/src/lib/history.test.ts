import { describe, expect, it } from "vitest";
import { clampSplit, delta, factHistory, hasHistory } from "./history";
import { mockSummary } from "./mock";
import type { Fact } from "./types";

describe("history", () => {
  const alpha = mockSummary("x", "y").facts.find((f) => f.id === "f2")!;
  it("orders newest first, without dose deltas for medication", () => {
    const h = factHistory(alpha);
    expect(h.map((e) => e.date)).toEqual(["2016-04-12", "2015-06-28"]);
    expect(h.map((e) => e.value)).toEqual(["1000 mg 1-0-1", "500 mg 1-0-1"]);
    expect(h.every((e) => e.delta === null)).toBe(true);
    expect(hasHistory(alpha)).toBe(true);
  });
  it("shows weight change in absolute and percent", () => {
    const src = (q: string, d: string) => ({ doc_id: "D", doc_title: "T", doc_date: d, quote: q, start: 0, end: 1, spans: [[0, 1]] as [number, number][], passage_ids: [], match: "exact" });
    const w: Fact = { id: "w", category: "body_measurements", label: "Gewicht", value: "70 kg", date: null, derived: false, checks: [], sources: [src("Gewicht 82 kg", "2015-01-01"), src("Gewicht 70 kg", "2016-01-01")] };
    const h = factHistory(w);
    expect(h[0]!.delta).toEqual({ abs: -12, pct: -14.6, unit: "kg" });
    expect(h[1]!.delta).toBeNull();
  });
  it("no delta across different units", () => expect(delta("70 kg", "170 cm")).toBeNull());
  it("limits the split to 20-70%", () => {
    expect(clampSplit(5)).toBe(20);
    expect(clampSplit(90)).toBe(70);
    expect(clampSplit(45)).toBe(45);
  });
});
