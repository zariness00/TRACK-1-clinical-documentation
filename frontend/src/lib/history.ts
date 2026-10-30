import type { Fact, Source } from "./types";

export type Delta = { abs: number; pct: number | null; unit: string };
export type HistoryEntry = { value: string; date: string | null; source: Source; delta: Delta | null };

/** Strip a leading label from a quoted value so the name isn't shown twice. */
export function stripLabel(value: string, label: string) {
  const v = value.trim(), l = label.trim();
  return l && v.toLowerCase().startsWith(l.toLowerCase()) && v.length > l.length ? v.slice(l.length).trim() : v;
}

const MEASURABLE_CATS = new Set(["body_measurements", "vitals", "vital_signs", "cardiac_function", "lab_values", "labs"]);
const MEASURABLE_RE = /gewicht|weight|gr(ö|oe)(ss|ß)e|height|bmi|lvef|\bef\b|ejektion|ejection|blutdruck|blood pressure|puls|pulse|herzfrequenz|heart rate|hba1c|temperatur|spo2|saturation/i;

/** True for quantifiable body data / vitals where numeric change is meaningful. */
export const isMeasurable = (f: Fact) => MEASURABLE_CATS.has(f.category) || MEASURABLE_RE.test(f.label);

/** First number and its unit in a text, e.g. "Gewicht 82 kg" → {n: 82, unit: "kg"}. */
export function firstNumber(s: string): { n: number; unit: string } | null {
  const m = s.match(/(-?\d+(?:[.,]\d+)?)\s*(%|[a-zA-Zµ/²]+)?/);
  if (!m) return null;
  return { n: parseFloat(m[1]!.replace(",", ".")), unit: m[2] ?? "" };
}

export function delta(newer: string, older: string): Delta | null {
  const a = firstNumber(newer), b = firstNumber(older);
  if (!a || !b || a.unit !== b.unit) return null;
  const abs = Math.round((a.n - b.n) * 100) / 100;
  const pct = b.n === 0 ? null : Math.round(((a.n - b.n) / b.n) * 1000) / 10;
  return { abs, pct, unit: a.unit };
}

/** Value history from a fact's verified sources, newest first. Values are the quoted text (label prefix stripped), never invented. */
export function factHistory(f: Fact): HistoryEntry[] {
  const sorted = [...f.sources].sort((a, b) => (b.doc_date ?? "").localeCompare(a.doc_date ?? ""));
  const vals = sorted.map((s) => stripLabel(s.quote, f.label));
  const measurable = isMeasurable(f);
  return sorted.map((s, i) => ({
    value: vals[i]!, date: s.doc_date, source: s,
    delta: measurable && i + 1 < vals.length ? delta(vals[i]!, vals[i + 1]!) : null,
  }));
}

/** True when a fact has sources from more than one date. */
export const hasHistory = (f: Fact) => new Set(f.sources.map((s) => s.doc_date)).size > 1;

export const SPLIT_MIN = 20;
export const SPLIT_MAX = 70;
export const clampSplit = (pct: number) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, pct));
