import type { Passage } from "./types";

const ENTITIES: Record<string, string> = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0",
  auml: "ä", ouml: "ö", uuml: "ü", Auml: "Ä", Ouml: "Ö", Uuml: "Ü", szlig: "ß", deg: "°", micro: "µ",
};

/** Clean raw document text once: <BR> -> newline, decode HTML entities. Nothing else changes. */
export function cleanText(raw: string): string {
  return raw
    .replace(/<BR\s*\/?>/gi, "\n")
    .replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e: string) => {
      if (e[0] === "#") {
        const code = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        return Number.isFinite(code) ? String.fromCodePoint(code) : m;
      }
      return ENTITIES[e] ?? m;
    });
}

const ABBR = ["dr", "med", "z. b", "z.b", "z. n", "z.n", "st.p", "st. p", "vd.a", "v.a", "geb", "ca", "bzw", "ggf", "li", "re", "bds", "tbl", "pat", "art", "diab", "mell", "wg", "gem", "ventr", "nr", "evtl", "inkl", "u.a", "sog", "vs", "prof", "mind", "max", "min", "std", "lt", "re", "chron", "rez", "st"];
const JOIN_END = /(?:^|\s)(der|die|das|den|dem|des|ein|eine|einer|einem|einen|eines|mit|von|vom|zu|zur|zum|in|im|am|an|bei|beim|und|oder|auf|für|nach|bis|über|unter|aus|durch|wegen|sowie)$/i;

type Kind = Passage["kind"];

export function lineKind(line: string): Kind {
  const t = line.trim();
  if (/\t|\S {3,}\S/.test(t) || (t.match(/(?:^|\s)\d(?=\s|$)/g)?.length ?? 0) >= 3) return "table_row";
  if (/^([-•*–·]|\d{1,2}[.)])\s+\S/.test(t)) return "list_item";
  if (t.endsWith(":") && t.length <= 80) return "heading";
  if (/^[^:.]{1,40}:\s*\S/.test(t)) return "field";
  return "line";
}

function isSentenceBoundary(text: string, dot: number): boolean {
  const before = text.slice(Math.max(0, dot - 12), dot + 1).toLowerCase();
  if (/(^|\D)\d{1,2}\.$/.test(before)) return false; // ordinals, day/month in dates
  if (/(^|[^a-zäöüß])[a-zäöüß]\.$/i.test(before)) return false; // single letter
  return !ABBR.some((a) => before.endsWith(`${a}.`) && !/[a-zäöüß]/i.test(before.charAt(before.length - a.length - 2)));
}

/** Split a document's cleaned text into citable passages. Invariant: text.slice(start, end) === passage.text. */
export function segmentDocument(docId: string, text: string): Passage[] {
  const lines: { start: number; end: number; t: string }[] = [];
  let pos = 0;
  for (const raw of text.split("\n")) {
    const lead = raw.length - raw.trimStart().length;
    const t = raw.trim();
    if (t) lines.push({ start: pos + lead, end: pos + lead + t.length, t });
    pos += raw.length + 1;
  }

  // Re-join wrapped prose lines.
  const units: { start: number; end: number; kind: Kind }[] = [];
  for (let i = 0; i < lines.length; i++) {
    const first = lines[i]!;
    const kind = lineKind(first.t);
    let end = first.end, last = first.t;
    if (kind === "line") {
      while (i + 1 < lines.length) {
        const next = lines[i + 1]!;
        const gap = text.slice(end, next.start);
        if ((gap.match(/\n/g)?.length ?? 0) > 2) break;
        if (lineKind(next.t) !== "line") break;
        const wraps = !/[.:;!?]$/.test(last) && /^[a-zäöüß0-9(]/.test(next.t);
        if (!wraps && !JOIN_END.test(last)) break;
        end = next.end; last = next.t; i++;
      }
    }
    units.push({ start: first.start, end, kind });
  }

  const out: Passage[] = [];
  const push = (start: number, end: number, kind: Kind) => {
    while (start < end && /\s/.test(text[start]!)) start++;
    while (end > start && /\s/.test(text[end - 1]!)) end--;
    if (end > start) out.push({ id: `${docId}#p${out.length + 1}`, n: out.length + 1, doc_id: docId, kind, start, end, text: text.slice(start, end) });
  };
  for (const u of units) {
    if (u.kind !== "line" && u.kind !== "list_item" && u.kind !== "field") { push(u.start, u.end, u.kind); continue; }
    const body = text.slice(u.start, u.end);
    const cuts: number[] = [];
    for (const m of body.matchAll(/\.\s+(?=[A-ZÄÖÜ0-9])/g)) if (isSentenceBoundary(body, m.index!)) cuts.push(m.index! + 1);
    if (cuts.length === 0) { push(u.start, u.end, u.kind === "line" && /[.!?]$/.test(body) ? "sentence" : u.kind); continue; }
    let s = 0;
    for (const c of [...cuts, body.length]) { push(u.start + s, u.start + c, "sentence"); s = c; }
  }
  return out;
}
