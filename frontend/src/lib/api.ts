import type { DocumentFull, Passage, Patient, SummaryResponse } from "./types";
import { MOCK_DOCUMENTS, MOCK_PATIENT, mockPassages, mockSummary } from "./mock";
import { verifySummary } from "./verify";

// Mock by default (Lovable preview). Set VITE_USE_MOCK=false in .env.local to use the real backend.
export const USE_MOCK = import.meta.env["VITE_USE_MOCK"] !== "false";
export const API_BASE = import.meta.env["VITE_API_BASE"] ?? "http://localhost:8000/api";

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`Server responded with ${res.status}`);
  return res.json() as Promise<T>;
}

export async function getSummary(patient_id: string, role: string, task: string): Promise<SummaryResponse> {
  if (USE_MOCK) { await delay(900); return verifySummary(mockSummary(role, task), MOCK_DOCUMENTS); }
  const [summary, patient] = await Promise.all([
    req<SummaryResponse>("/summary", { method: "POST", body: JSON.stringify({ patient_id, role, task }) }),
    getPatient(patient_id),
  ]);
  const docs = await Promise.all(patient.documents.map((d) => getDocument(patient_id, d.doc_id)));
  const verified = verifySummary(summary, docs);
  // Keep what the backend's own validator rejected alongside what was rejected here.
  return { ...verified, rejected: [...summary.rejected, ...verified.rejected] };
}

export async function getPatient(id: string): Promise<Patient> {
  if (USE_MOCK) { await delay(150); return MOCK_PATIENT; }
  return req(`/patients/${encodeURIComponent(id)}`);
}

export async function getDocument(id: string, docId: string): Promise<DocumentFull> {
  if (USE_MOCK) {
    await delay(150);
    const d = MOCK_DOCUMENTS.find((x) => x.doc_id === docId);
    if (!d) throw new Error("Document not found");
    return d;
  }
  return req(`/patients/${encodeURIComponent(id)}/documents/${encodeURIComponent(docId)}`);
}

export async function searchPassages(id: string, q: string, k = 10): Promise<Passage[]> {
  if (USE_MOCK) { await delay(150); return mockPassages(q).slice(0, k); }
  return req(`/patients/${encodeURIComponent(id)}/passages?q=${encodeURIComponent(q)}&k=${k}`);
}

export async function listPatients(): Promise<Patient[]> {
  if (USE_MOCK) { await delay(150); return [MOCK_PATIENT]; }
  // The list endpoint has no document details, and the patient list screen needs them.
  const list = await req<{ patient_id: string }[]>("/patients");
  return Promise.all(list.map((p) => getPatient(p.patient_id)));
}
