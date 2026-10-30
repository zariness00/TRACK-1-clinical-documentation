import type { Fact, Missing, SummaryResponse } from "./types";

type Profile = { task: RegExp; topics: { categories: string[]; terms: RegExp }[] };

// Task descriptions win over profession, so changing tasks also changes priorities.
const profiles: Profile[] = [
  { task: /anaesthe|anesthe|pre.?operat/i, topics: [
    { categories: ["antithrombotic", "current_medication", "medication"], terms: /antithrombot|anticoagul|antiplatelet|blood thinner|aspirin|clopidogrel|apixaban|rivaroxaban|warfarin/i },
    { categories: ["allergies", "allergy"], terms: /allerg/i },
    { categories: ["cardiac_function", "cardiac", "cardiology"], terms: /ejection|lvef|ventricular/i },
  ] },
  { task: /tumou?r board|surgery planning|surgeon/i, topics: [
    { categories: ["histology", "staging", "tumour", "oncology"], terms: /histolog|tnm|stage|carcinoma/i },
    { categories: ["receptor_status", "biomarkers"], terms: /receptor|ki.?67|her2|oestrogen|estrogen|progesterone/i },
    { categories: ["treatment_plan", "surgery", "tumour_board"], terms: /tumou?r board|recommend|scheduled|quadrantectomy|radiotherapy/i },
  ] },
  { task: /diabet|glucose|hba1c/i, topics: [
    { categories: ["current_medication", "medication", "diabetes_medication"], terms: /glucose|metformin|insulin|gliclazide|glimepiride|sitagliptin/i },
    { categories: ["glycaemic_control", "hba1c", "diabetes"], terms: /hba1c/i },
    { categories: ["body_measurements", "weight"], terms: /weight|gewicht/i },
  ] },
  { task: /dizz|blurred|night duty|emergency/i, topics: [
    { categories: ["neurology", "history", "carotid"], terms: /dizz|blurred|carotid|stenosis|amaurosis/i },
    { categories: ["allergies", "renal_function", "contrast"], terms: /contrast|iodin|allerg|creatinine|egfr|renal/i },
    { categories: ["current_medication", "medication", "hypertension"], terms: /antihypertensive|blood pressure|ramipril|amlodipine|bisoprolol|losartan/i },
  ] },
  { task: /follow.?up|still open/i, topics: [
    { categories: ["follow_up", "recommendations"], terms: /follow.?up|recommend|kontrolle/i },
    { categories: ["carotid", "vascular", "imaging"], terms: /carotid|stenosis|ultrasound/i },
  ] },
];

export function rankSummary(summary: SummaryResponse) {
  const profile = profiles.find((p) => p.task.test(summary.task)) ?? profiles.find((p) => p.task.test(summary.role));
  const taskTerms = summary.task.toLowerCase().match(/[a-z]{4,}/g) ?? [];
  function score(category: string, text: string) {
    if (profile) {
      return Math.max(0, ...profile.topics.map((topic, index) => {
        const match = topic.terms.test(text);
        const categoryMatch = topic.categories.includes(category);
        return match || categoryMatch ? 100 - index * 10 + (match ? 5 : 0) : 0;
      }));
    }
    return taskTerms.some((term) => `${category} ${text}`.toLowerCase().includes(term)) ? 60 : 0;
  }
  const factScore = (f: Fact) => score(f.category, `${f.label} ${f.value}`);
  const missingScore = (m: Missing) => score(m.category, m.item);
  const facts = [...summary.facts].sort((a, b) => factScore(b) - factScore(a));
  const missing = [...summary.not_documented].sort((a, b) => missingScore(b) - missingScore(a));
  const contradictions = [...summary.contradictions].sort((a, b) => score(b.category, b.summary) - score(a.category, a.summary));
  const categories = [...new Set([...summary.categories, ...facts.map((f) => f.category), ...missing.map((m) => m.category)])]
    .sort((a, b) => {
      const categoryScore = (c: string) => Math.max(score(c, ""), ...facts.filter((f) => f.category === c).map(factScore), ...missing.filter((m) => m.category === c).map(missingScore));
      return categoryScore(b) - categoryScore(a);
    });
  return {
    priorityFacts: facts.filter((f) => factScore(f) > 0),
    otherFacts: facts.filter((f) => factScore(f) === 0),
    priorityMissing: missing.filter((m) => missingScore(m) > 0),
    otherMissing: missing.filter((m) => missingScore(m) === 0),
    relevantContradictions: contradictions.filter((c) => score(c.category, c.summary) > 0),
    otherContradictions: contradictions.filter((c) => score(c.category, c.summary) === 0),
    categories,
  };
}