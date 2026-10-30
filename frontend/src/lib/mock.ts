// Invented demo data only. No real clinical text.
import type { DocumentFull, Passage, Patient, Source, SummaryResponse } from "./types";

const DOC_A_TEXT = `Entlassungsbrief (Testdokument)

Diagnosen:
Testdiagnose A, erstmals 2014 festgestellt.
Testdiagnose B.

Allergien:
Allergie gegen Testsubstanz X (Hautausschlag).

Medikation bei Entlassung:
Testmedikament Alpha 500 mg 1-0-1
Testmedikament Beta 10 mg 0-0-1

Befunde:
Gewicht 70 kg, Groesse 170 cm.
Laborwert Q im Normbereich.

Empfehlung:
Kontrolle beim Hausarzt in 4 Wochen.`;

const DOC_B_TEXT = `Ambulanzbericht (Testdokument)

Anamnese:
Patientin berichtet ueber keine bekannten Allergien.

Aktuelle Medikation:
Testmedikament Alpha 1000 mg 1-0-1

Procedere:
Geplanter Eingriff am 16.05.2016.
Kontrolle in 3 Monaten.`;

export const MOCK_DOCUMENTS: DocumentFull[] = [
  { doc_id: "DOC-A", title: "Discharge letter", date: "2015-06-28", type: "letter", department: "Internal Medicine", text: DOC_A_TEXT },
  { doc_id: "DOC-B", title: "Outpatient report", date: "2016-04-12", type: "report", department: "Surgery", text: DOC_B_TEXT },
];

export const MOCK_PATIENT: Patient = {
  patient_id: "0000-0001",
  name: "Test Patient",
  documents: MOCK_DOCUMENTS.map(({ text: _t, ...m }) => m),
};

/** Build a Source; pass several parts for a quote with gaps. Offsets via indexOf. */
export function src(docId: string, ...parts: string[]): Source {
  const doc = MOCK_DOCUMENTS.find((d) => d.doc_id === docId);
  if (!doc) throw new Error(`Document not found: ${docId}`);
  const spans: [number, number][] = [];
  let from = 0;
  for (const p of parts) {
    const i = doc.text.indexOf(p, from);
    if (i < 0) throw new Error(`Quote not found in ${docId}: ${p}`);
    spans.push([i, i + p.length]);
    from = i + p.length;
  }
  const first = spans[0];
  const last = spans.at(-1);
  if (!first || !last) throw new Error(`No quote supplied for ${docId}`);
  return {
    doc_id: docId, doc_title: doc.title, doc_date: doc.date,
    quote: parts.join(" … "), start: first[0], end: last[1],
    spans, passage_ids: [`${docId}-p1`], match: "exact",
  };
}

export function mockSummary(role: string, task: string): SummaryResponse {
  return {
    patient_id: "0000-0001", patient_name: "Test Patient", role, task,
    categories: ["allergies", "current_medication", "body_measurements"],
    facts: [
      { id: "f1", category: "allergies", label: "Allergy", value: "Allergie gegen Testsubstanz X (Hautausschlag).", date: "2015-06-28", derived: false, sources: [src("DOC-A", "Allergie gegen Testsubstanz X (Hautausschlag).")], checks: [] },
      { id: "f2", category: "current_medication", label: "Testmedikament Alpha", value: "Testmedikament Alpha 1000 mg 1-0-1", date: "2016-04-12", derived: false, sources: [src("DOC-B", "Testmedikament Alpha 1000 mg 1-0-1"), src("DOC-A", "Testmedikament Alpha 500 mg 1-0-1")], checks: [] },
      { id: "f3", category: "current_medication", label: "Testmedikament Beta", value: "Testmedikament Beta 10 mg 0-0-1", date: "2015-06-28", derived: false, sources: [src("DOC-A", "Testmedikament Beta 10 mg 0-0-1")], checks: [] },
      { id: "f4", category: "body_measurements", label: "Weight / height", value: "70 kg / 170 cm", date: "2015-06-28", derived: false, sources: [src("DOC-A", "Gewicht 70 kg", "Groesse 170 cm")], checks: [] },
      { id: "f5", category: "body_measurements", label: "BMI", value: "24.2 kg/m²", date: "2015-06-28", derived: true, sources: [src("DOC-A", "Gewicht 70 kg, Groesse 170 cm.")], checks: [] },
    ],
    not_documented: [
      { category: "body_measurements", item: "Current weight (2016)", searched: ["Gewicht", "kg", "Körpergewicht"] },
      { category: "airway", item: "Airway assessment", searched: ["Atemweg", "Mallampati", "Intubation"] },
    ],
    contradictions: [
      { id: "c1", category: "allergies", summary: "Allergy status differs between documents.", claims: [
        { statement: "Allergy to test substance X documented.", source: src("DOC-A", "Allergie gegen Testsubstanz X (Hautausschlag).") },
        { statement: "No known allergies reported.", source: src("DOC-B", "Patientin berichtet ueber keine bekannten Allergien.") },
      ] },
      { id: "c2", category: "follow_up", summary: "Follow-up interval differs.", claims: [
        { statement: "Follow-up in 4 weeks.", source: src("DOC-A", "Kontrolle beim Hausarzt in 4 Wochen.") },
        { statement: "Follow-up in 3 months.", source: src("DOC-B", "Kontrolle in 3 Monaten.") },
      ] },
    ],
    rejected: [], timings_ms: { total: 900 }, mock: true,
  };
}

export function mockPassages(q: string): Passage[] {
  const out: Passage[] = [];
  for (const d of MOCK_DOCUMENTS) {
    let pos = 0;
    for (const line of d.text.split("\n")) {
      if (line.toLowerCase().includes(q.toLowerCase())) {
        out.push({ id: `${d.doc_id}-${pos}`, doc_id: d.doc_id, kind: line.trim().endsWith(":") ? "heading" : "line", start: pos, end: pos + line.length, text: line });
      }
      pos += line.length + 1;
    }
  }
  return out;
}
