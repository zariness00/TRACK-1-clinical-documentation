import type { Claim, DocumentFull, Fact, Source, SummaryResponse } from "./types";
import { segmentDocument } from "./segment";

/** Normalize text and keep a map from each normalized char to its raw index. */
export function normalize(raw: string): { norm: string; map: number[] } {
  let norm = ""; const map: number[] = [];
  for (let i = 0; i < raw.length; i++) {
    let c = raw[i]!;
    if (c === "\u00ad") continue; // soft hyphen
    if (c === "-" && raw[i + 1] === "\n" && /\p{L}/u.test(raw[i - 1] ?? "") && /\p{L}/u.test(raw[i + 2] ?? "")) { i++; continue; }
    if (/\s/.test(c)) {
      if (norm.endsWith(" ")) continue;
      c = " ";
    } else if (/[„“”«»″]/.test(c)) c = '"';
    else if (/[‚‘’′]/.test(c)) c = "'";
    else if (/[–—‑‒]/.test(c)) c = "-";
    norm += c; map.push(i);
  }
  return { norm, map };
}

const cutsNumber = (text: string, a: number, b: number) =>
  (/\d/.test(text[a] ?? "") && /[\d]/.test(text[a - 1] ?? "")) ||
  (/\d/.test(text[a] ?? "") && /[.,]/.test(text[a - 1] ?? "") && /\d/.test(text[a - 2] ?? "")) ||
  (/\d/.test(text[b - 1] ?? "") && (/\d/.test(text[b] ?? "") || (/[.,]/.test(text[b] ?? "") && /\d/.test(text[b + 1] ?? ""))));

function findIn(hay: string, needle: string, ok: (a: number, b: number) => boolean): number {
  for (let i = hay.indexOf(needle); i >= 0; i = hay.indexOf(needle, i + 1)) if (ok(i, i + needle.length)) return i;
  return -1;
}

export type Located = { doc_id: string; start: number; end: number; match: string };

/** Locate a verbatim quote, strictest level first. Returns null if it cannot be verified. */
export function locateQuote(docs: DocumentFull[], docId: string, quote: string): Located | null {
  const q = quote.trim();
  if (!q) return null;
  const order = [docs.find((d) => d.doc_id === docId), ...docs.filter((d) => d.doc_id !== docId)].filter(Boolean) as DocumentFull[];
  for (const [di, doc] of order.entries()) {
    const text = doc.text;
    if (q.length < 5 && !segmentDocument(doc.doc_id, text).some((p) => p.text === q)) continue;
    const okRaw = (a: number, b: number) => !cutsNumber(text, a, b);
    const tag = (m: string) => (di === 0 ? m : `other_doc_${m}`);
    const i = findIn(text, q, okRaw);
    if (i >= 0) return { doc_id: doc.doc_id, start: i, end: i + q.length, match: tag("exact") };
    const { norm, map } = normalize(text);
    const nq = normalize(q).norm;
    const okNorm = (a: number, b: number) => okRaw(map[a]!, map[b - 1]! + 1);
    for (const [level, h, n] of [["normalized", norm, nq], ["case_insensitive", norm.toLowerCase(), nq.toLowerCase()]] as const) {
      const j = findIn(h, n, okNorm);
      if (j >= 0) return { doc_id: doc.doc_id, start: map[j]!, end: map[j + n.length - 1]! + 1, match: tag(level) };
    }
  }
  return null;
}

const ROMAN: Record<string, number> = { I: 1, II: 2, III: 3, IV: 4, V: 5, VI: 6, VII: 7, VIII: 8, IX: 9, X: 10, XI: 11, XII: 12 };
function numbers(s: string, roman: boolean): number[] {
  const out = (s.match(/\d+(?:[.,]\d+)?/g) ?? []).map((n) => parseFloat(n.replace(",", ".")));
  if (roman) for (const m of s.matchAll(/\b([IVX]{1,4})\b/g)) if (ROMAN[m[1]!]) out.push(ROMAN[m[1]!]!);
  return out;
}

/** Check a displayed value against its German quotes. Returns a rejection reason or null. */
export function checkValue(value: string, quotes: string[], derived: boolean): string | null {
  const q = quotes.join(" \n ");
  if (!derived) {
    const have = new Set(numbers(q, true));
    const miss = numbers(value, true).find((n) => !have.has(n));
    if (miss !== undefined) return `number ${miss} not in quote`;
  }
  const left = /\blinks|\bli\.|\bsin\.|sinist/i, right = /\brechts|\bre\.|\bdex\.|dext/i;
  if (/\bleft\b/i.test(value) && !left.test(q)) return "laterality: left not in quote";
  if (/\bright\b/i.test(value) && !right.test(q)) return "laterality: right not in quote";
  if (/\bpositive\b/i.test(value) && /negativ/i.test(q) && !/positiv/i.test(q)) return "polarity flipped";
  if (/\bnegative\b/i.test(value) && /positiv/i.test(q) && !/negativ/i.test(q)) return "polarity flipped";
  const affirmative = !/\b(no|not|none|without|negative|denies|absent|kein|keine|nicht|ohne)\b/i.test(value);
  if (affirmative && quotes.some((x) => x.length <= 80 && !/[,;]/.test(x) && /\b(kein|keine|keinen|keiner|keinem|nicht|ohne)\b/i.test(x))) return "negation dropped";
  if (/Vd\.\s?a\.|V\.\s?a\.|Verdacht|suspekt|\?|Fragestellung/i.test(q) && !/suspect|suspicion|possible|query|question|uncertain|\?|V\.a\.|Verdacht/i.test(value)) return "uncertain finding shown as confirmed";
  return null;
}

function verifySource(docs: DocumentFull[], s: Source): Source | null {
  const parts = s.quote.split(" … ");
  const spans: [number, number][] = [];
  let docId = s.doc_id, match = "exact";
  for (const p of parts) {
    const loc = locateQuote(docs, docId, p);
    if (!loc) return null;
    docId = loc.doc_id; if (loc.match !== "exact") match = loc.match;
    spans.push([loc.start, loc.end]);
  }
  const doc = docs.find((d) => d.doc_id === docId)!;
  const ps = segmentDocument(docId, doc.text).filter((p) => spans.some(([a, b]) => p.start < b && a < p.end));
  return { ...s, doc_id: docId, doc_title: doc.title, doc_date: doc.date, quote: spans.map(([a, b]) => doc.text.slice(a, b)).join(" … "), start: spans[0]![0], end: spans.at(-1)![1], spans, passage_ids: ps.map((p) => p.id), match };
}

export type Rejection = { kind: "fact" | "claim"; id: string; reason: string };

export function verifyFact(docs: DocumentFull[], f: Fact): { fact: Fact | null; reason?: string } {
  const sources = f.sources.map((s) => verifySource(docs, s)).filter(Boolean) as Source[];
  if (!sources.length) return { fact: null, reason: "no verified source" };
  const reason = checkValue(f.value, sources.map((s) => s.quote), f.derived);
  return reason ? { fact: null, reason } : { fact: { ...f, sources } };
}

/** Drop everything that cannot be traced to a verified passage; never render unverified text. */
export function verifySummary(s: SummaryResponse, docs: DocumentFull[]): SummaryResponse & { rejected: Rejection[] } {
  const rejected: Rejection[] = [];
  const facts: Fact[] = [];
  for (const f of s.facts) {
    const r = verifyFact(docs, f);
    if (r.fact) facts.push(r.fact); else rejected.push({ kind: "fact", id: f.id, reason: r.reason! });
  }
  const contradictions = s.contradictions.map((c) => {
    const claims: Claim[] = [];
    c.claims.forEach((cl, i) => {
      const src = verifySource(docs, cl.source);
      const reason = src ? checkValue(cl.statement, [src.quote], true) : "no verified source";
      if (src && !reason) claims.push({ ...cl, source: src }); else rejected.push({ kind: "claim", id: `${c.id}-${i}`, reason: reason! });
    });
    return { ...c, claims };
  }).filter((c) => c.claims.length >= 2);
  if (rejected.length) console.info("Rejected unverified items", rejected);
  return { ...s, facts, contradictions, rejected };
}
