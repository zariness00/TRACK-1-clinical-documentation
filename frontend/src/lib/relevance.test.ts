import { describe, expect, it } from "vitest";
import { mockSummary } from "./mock";
import { rankSummary } from "./relevance";
import type { Fact } from "./types";

const fact = (id: string, category: string, label: string): Fact => ({ id, category, label, value: "Documented example", sources: [], checks: [], date: null, derived: false });

describe("task-first evidence selection", () => {
  const cases = [
    { task: "Pre-operative anaesthesia check", expected: ["blood", "allergy", "heart"], facts: [fact("heart", "cardiac_function", "LVEF"), fact("allergy", "allergies", "Allergy"), fact("blood", "antithrombotic", "Blood thinner and dose")] },
    { task: "Surgery planning after the tumour board", expected: ["histology", "receptors", "plan"], facts: [fact("plan", "treatment_plan", "Scheduled operation"), fact("receptors", "receptor_status", "Ki-67"), fact("histology", "histology", "TNM stage")] },
    { task: "Diabetes review in spring 2016", expected: ["drugs", "hba1c", "weight"], facts: [fact("weight", "body_measurements", "Weight change"), fact("hba1c", "glycaemic_control", "HbA1c and date"), fact("drugs", "diabetes_medication", "Glucose-lowering drugs and dose")] },
    { task: "Night duty: dizziness and blurred vision in the left eye", expected: ["history", "contrast", "bp"], facts: [fact("bp", "hypertension", "Blood pressure medication and dose"), fact("contrast", "contrast", "Iodinated contrast"), fact("history", "neurology", "Prior dizziness and cause")] },
    { task: "Check which follow-ups are still open", expected: ["follow", "scan"], facts: [fact("scan", "carotid", "Carotid ultrasound and stenosis side"), fact("follow", "follow_up", "Discharge recommendations")] },
  ];
  for (const c of cases) {
    it(`prioritises the reference topics for ${c.task}`, () => {
      const summary = { ...mockSummary("Anaesthetist", c.task), facts: [...c.facts, fact("other", "unrelated", "Other finding")], not_documented: [] };
      const result = rankSummary(summary);
      expect(result.priorityFacts.map((f) => f.id)).toEqual(c.expected);
      expect(result.otherFacts.map((f) => f.id)).toEqual(["other"]);
    });
  }
  it("preserves every returned fact, missing item and contradiction exactly once", () => {
    const summary = mockSummary("Anaesthetist", "Pre-operative anaesthesia check");
    const result = rankSummary(summary);
    expect([...result.priorityFacts, ...result.otherFacts].map((f) => f.id).sort()).toEqual(summary.facts.map((f) => f.id).sort());
    expect([...result.priorityMissing, ...result.otherMissing].map((m) => m.item).sort()).toEqual(summary.not_documented.map((m) => m.item).sort());
    expect([...result.relevantContradictions, ...result.otherContradictions].map((c) => c.id).sort()).toEqual(summary.contradictions.map((c) => c.id).sort());
  });
});