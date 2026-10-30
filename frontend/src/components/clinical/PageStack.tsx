import { useEffect, useMemo, useRef, useState } from "react";
import type { DocumentFull } from "@/lib/types";
import { formatDate } from "@/lib/categories";

export type Mark = { doc_id: string; a: number; b: number; rows: number[]; cfs: number[]; cave?: boolean };
export type ScrollTarget = { doc_id: string; a: number; nonce: number } | null;
export const rowColor = (n: number) => `var(--row-${((n - 1) % 12) + 1})`;

type Seg = { a: number; b: number; marks: Mark[] };

function segments(text: string, marks: Mark[]): Seg[] {
  const cuts = [...new Set([0, text.length, ...marks.flatMap((m) => [m.a, m.b])])].sort((x, y) => x - y);
  const out: Seg[] = [];
  for (let i = 0; i < cuts.length - 1; i++) {
    const a = cuts[i]!, b = cuts[i + 1]!;
    if (b > a) out.push({ a, b, marks: marks.filter((m) => m.a < b && a < m.b) });
  }
  return out;
}

export function orderDocuments(docs: DocumentFull[], marks: Mark[]) {
  const byDate = (x: DocumentFull, y: DocumentFull) => (x.date ?? "").localeCompare(y.date ?? "");
  const has = (d: DocumentFull, f: (m: Mark) => boolean) => marks.some((m) => m.doc_id === d.doc_id && f(m));
  const answers = docs.filter((d) => has(d, (m) => m.rows.length > 0)).sort(byDate);
  const conflicts = docs.filter((d) => !answers.includes(d) && has(d, (m) => m.cfs.length > 0)).sort(byDate);
  const rest = docs.filter((d) => !answers.includes(d) && !conflicts.includes(d)).sort(byDate);
  return { ordered: [...answers, ...conflicts, ...rest], rest: new Set(rest.map((d) => d.doc_id)) };
}

export function PageStack({ docs, marks, hoverRow, target, onActiveRows }: {
  docs: DocumentFull[]; marks: Mark[]; hoverRow: number | null; target: ScrollTarget; onActiveRows: (rows: number[]) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const { ordered, rest } = useMemo(() => orderDocuments(docs, marks), [docs, marks]);

  useEffect(() => {
    const c = scrollRef.current;
    if (!c) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const box = c.getBoundingClientRect();
        const line = box.top + box.height / 3;
        const pages = [...c.querySelectorAll<HTMLElement>("[data-page]")];
        const idx = pages.findIndex((p) => p.getBoundingClientRect().bottom > line);
        setPage(Math.max(0, idx));
        let best: HTMLElement | null = null, dist = Infinity;
        c.querySelectorAll<HTMLElement>(".hl[data-rows]").forEach((el) => {
          const r = el.getBoundingClientRect();
          if (r.bottom < box.top || r.top > box.bottom) return;
          const d = Math.abs(r.top - line);
          if (d < dist) { dist = d; best = el; }
        });
        const rows = (best as HTMLElement | null)?.dataset["rows"]?.split(" ").filter(Boolean).map(Number) ?? [];
        onActiveRows(rows);
      });
    };
    onScroll();
    c.addEventListener("scroll", onScroll, { passive: true });
    return () => { c.removeEventListener("scroll", onScroll); cancelAnimationFrame(raf); };
  }, [ordered, onActiveRows]);

  useEffect(() => {
    const c = scrollRef.current;
    if (!c || !target) return;
    const els = [...c.querySelectorAll<HTMLElement>(`.hl[data-doc="${CSS.escape(target.doc_id)}"]`)];
    const el = els.find((e) => Number(e.dataset["a"]) <= target.a && target.a < Number(e.dataset["b"])) ?? c.querySelector<HTMLElement>(`[data-page="${CSS.escape(target.doc_id)}"]`);
    if (!el) return;
    c.scrollTo({ top: c.scrollTop + el.getBoundingClientRect().top - c.getBoundingClientRect().top - c.clientHeight / 3 + 10, behavior: "smooth" });
    el.animate?.([{ outlineColor: "var(--foreground)" }, { outlineColor: "transparent" }], { duration: 1200 });
  }, [target]);

  const goTo = (i: number) => {
    const c = scrollRef.current, el = c?.querySelectorAll<HTMLElement>("[data-page]")[i];
    if (c && el) c.scrollTo({ top: el.offsetTop - 16, behavior: "smooth" });
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted">
      <div ref={scrollRef} className="relative min-h-0 flex-1 space-y-6 overflow-auto px-6 py-5">
        {ordered.map((d) => {
          const dm = marks.filter((m) => m.doc_id === d.doc_id);
          const faded = rest.has(d.doc_id);
          return (
            <article key={d.doc_id} data-page={d.doc_id} className={`doc-page mx-auto max-w-[46rem] rounded-sm px-10 py-9 text-foreground shadow-md ${faded ? "opacity-60" : ""}`}>
              <header className="mb-5 border-b-2 border-foreground pb-3">
                <div className="flex justify-between gap-4 text-sm uppercase tracking-wider text-muted-foreground"><span>{d.department}</span><span>{formatDate(d.date)}</span></div>
                <h3 className="mt-1 text-xl font-semibold">{d.title}</h3>
                <div className="text-sm text-muted-foreground">Doc {d.doc_id} · {d.type}</div>
                {faded && <div className="mt-2 font-sans text-sm italic text-muted-foreground">No passages relevant to this task</div>}
              </header>
              <div className="whitespace-pre-wrap break-words text-[15px] leading-relaxed">
                {segments(d.text, dm).map((s) => {
                  const t = d.text.slice(s.a, s.b);
                  if (!s.marks.length) return <span key={s.a}>{t}</span>;
                  const rows = [...new Set(s.marks.flatMap((m) => m.rows))].sort((x, y) => x - y);
                  const cfs = [...new Set(s.marks.flatMap((m) => m.cfs))];
                  const starts = s.marks.filter((m) => m.a === s.a);
                  const color = rows.length ? rowColor(rows[0]!) : "var(--cave)";
                  const startRows = [...new Set(starts.flatMap((m) => m.rows))].sort((x, y) => x - y);
                  const startConflict = starts.some((m) => m.cfs.length);
                  const cls = ["hl", !rows.length ? "hl-conflict-only" : "", cfs.length && rows.length ? "hl-conflict" : "", hoverRow !== null && rows.includes(hoverRow) ? "hl-pulse" : ""].join(" ");
                  return (
                    <span key={s.a} className={cls} style={{ ["--c" as string]: color }} data-rows={rows.join(" ")} data-cfs={cfs.join(" ")} data-doc={d.doc_id} data-a={s.a} data-b={s.b}>
                      {startRows.map((r) => <span key={r} className="hl-badge" style={{ ["--c" as string]: rowColor(r) }} aria-label={`Answer ${r}`}>{r}</span>)}
                      {startConflict && <span className="hl-badge" style={{ ["--c" as string]: "var(--cave)" }} aria-label="Conflicting passage">!</span>}
                      {t}
                    </span>
                  );
                })}
              </div>
            </article>
          );
        })}
      </div>
      <nav aria-label="Pages" className="flex shrink-0 items-center gap-3 border-t bg-card px-4 py-2">
        <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto py-1">
          {ordered.map((d, i) => (
            <button key={d.doc_id} type="button" onClick={() => goTo(i)} aria-label={`Go to ${d.title}`} aria-current={i === page}
              className={`relative h-16 w-12 shrink-0 rounded-[2px] border bg-background shadow-sm focus-visible:outline-2 focus-visible:outline-ring ${i === page ? "ring-2 ring-primary" : ""} ${rest.has(d.doc_id) ? "opacity-60" : ""}`}>
              {marks.filter((m) => m.doc_id === d.doc_id).map((m, k) => (
                <span key={k} className="absolute left-1.5 right-1.5 h-[3px] rounded-full" style={{ top: `${8 + (m.a / Math.max(1, d.text.length)) * 80}%`, background: m.rows.length ? rowColor(m.rows[0]!) : "var(--cave)" }} />
              ))}
            </button>
          ))}
        </div>
        <div className="shrink-0 text-sm text-muted-foreground">Page {Math.min(page + 1, ordered.length)} / {ordered.length}</div>
      </nav>
    </div>
  );
}
