export type Source = {
  doc_id: string; doc_title: string; doc_date: string | null;
  quote: string; start: number; end: number;
  spans: [number, number][]; // character offsets into the document text; more than one if the quote has gaps
  passage_ids: string[]; match: string;
};
export type Fact = {
  id: string; category: string; label: string; value: string;
  date: string | null; derived: boolean; sources: Source[]; checks: string[];
};
export type Claim = { statement: string; source: Source };
export type Contradiction = { id: string; category: string; summary: string; claims: Claim[] };
export type Missing = { category: string; item: string; searched: string[] };
export type SummaryResponse = {
  patient_id: string; patient_name: string; role: string; task: string;
  categories: string[];
  facts: Fact[]; not_documented: Missing[]; not_checked?: string[]; contradictions: Contradiction[];
  rejected: unknown[]; timings_ms: Record<string, number>; mock: boolean;
};

export type DocumentMeta = { doc_id: string; title: string; date: string | null; type: string; department: string };
export type Patient = { patient_id: string; name: string; documents: DocumentMeta[] };
export type DocumentFull = DocumentMeta & { text: string };
export type Passage = { id: string; n?: number; doc_id: string; kind: string; start: number; end: number; text: string };
