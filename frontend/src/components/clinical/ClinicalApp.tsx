import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageStack, rowColor, type Mark, type ScrollTarget } from "./PageStack";
import { Link, useNavigate } from "@tanstack/react-router";
import { AppHeader } from "./AppHeader";
import { getDocument, getPatient, getSummary, searchPassages, USE_MOCK } from "@/lib/api";
import { QUICK } from "@/lib/session";
import { categoryTitle, formatDate } from "@/lib/categories";
import { rankSummary } from "@/lib/relevance";
import { Button } from "@/components/ui/button";
import { clampSplit, factHistory, hasHistory, stripLabel, type Delta } from "@/lib/history";
import type { Contradiction, DocumentFull, Fact, Missing, Passage, Patient, Source, SummaryResponse } from "@/lib/types";

function passageToSource(p: Passage, patient: Patient | null): Source {
  const d = patient?.documents.find((x) => x.doc_id === p.doc_id);
  return { doc_id: p.doc_id, doc_title: d?.title ?? p.doc_id, doc_date: d?.date ?? null, quote: p.text, start: p.start, end: p.end, spans: [[p.start, p.end]], passage_ids: [p.id], match: "passage" };
}

type Open = (s: Source) => void;

function SourceChip({ source, onOpen }: { source: Source; onOpen: Open }) {
  const label = [source.doc_title, formatDate(source.doc_date)].filter(Boolean).join(" · ");
  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); onOpen(source); }} aria-label={`Show source: ${label}`}
      className="text-left text-sm text-accent-foreground underline decoration-dotted underline-offset-2 hover:decoration-solid">
      <span aria-hidden>¶ </span>{label}
    </button>
  );
}

// Gaps in the analysis itself: never to be read as "not documented" or as a complete screen.
// Items rejected by verification are not shown to clinicians (only logged in the browser console by verify.ts).
function AnalysisNotices({ s }: { s: SummaryResponse }) {
  const notChecked = s.not_checked ?? [];
  if (!notChecked.length) return null;
  return (
    <div role="alert" className="mb-4 space-y-2 rounded-sm border border-cave bg-cave-bg px-3 py-2 text-cave">
      {notChecked.length > 0 && (
        <div>
          <div className="font-bold">Analysis incomplete: {notChecked.length} {notChecked.length === 1 ? "item" : "items"} not checked</div>
          <div className="text-sm">The AI ran out of time or failed. This does not mean they are absent from the record. Check the documents or try again.</div>
          <ul className="mt-1 list-disc pl-5 text-sm">{notChecked.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function MissingLine({ m }: { m: Missing }) {
  return (
    <div className="border-b border-border/60 py-2 text-missing">
      <span className="text-sm">{m.item}: </span><span className="font-medium italic">Not documented</span>
      {m.searched.length > 0 && <div className="text-xs">Searched for: {m.searched.join(", ")}</div>}
    </div>
  );
}

function ConflictLine({ c, onOpen }: { c: Contradiction; onOpen: Open }) {
  return (
    <div className="border-l-4 border-conflict-border bg-conflict-bg/60 py-2 pl-3 pr-2">
      <div className="flex items-start gap-2 font-semibold text-conflict">
        <span aria-hidden>!</span><span><span className="sr-only">Contradiction: </span>{c.summary}</span>
      </div>
      <ul className="mt-1 space-y-1">
        {c.claims.map((cl, i) => <li key={i} className="flex flex-wrap items-center gap-2 text-sm"><span>{cl.statement}</span><SourceChip source={cl.source} onOpen={onOpen} /></li>)}
      </ul>
    </div>
  );
}

function DeltaTag({ d }: { d: Delta }) {
  if (d.abs === 0) return <span className="text-xs text-foreground">± 0 {d.unit}</span>;
  const up = d.abs > 0;
  const sign = up ? "+" : "−";
  return (
    <span className="text-xs font-medium text-foreground" aria-label={`${up ? "Increased" : "Decreased"} by ${Math.abs(d.abs)} ${d.unit}`}>
      <span aria-hidden>{up ? "▲" : "▼"}</span> {sign}{Math.abs(d.abs)} {d.unit}{d.pct !== null && ` (${sign}${Math.abs(d.pct)}%)`}
    </span>
  );
}

/** Hide labels that just repeat the topic heading (e.g. "Allergy" under "Allergies"). */
function labelRepeatsTopic(label: string, category: string) {
  const l = label.trim().toLowerCase(), t = categoryTitle(category).toLowerCase();
  return l.length >= 4 && t.includes(l.slice(0, Math.max(4, l.length - 1)));
}
/** Display-only: drop the label and a leading "Allergie gegen"/"Allergy to" from the shown value. */
function displayValue(f: Fact) {
  let v = stripLabel(f.value, f.label);
  if (f.category.toLowerCase() === "allergies") v = v.replace(/^(allergie\s+gegen|allergy\s+to)\s+/i, "");
  return v;
}

function FactRow({ f, n, lit, onHover, onClickRow, onOpen }: { f: Fact; n: number; lit: boolean; onHover: (n: number | null) => void; onClickRow: (f: Fact) => void; onOpen: Open }) {
  const c = rowColor(n);
  // Medication always uses the same layout: drug name as heading, dose line(s) below, newest first.
  const hist = hasHistory(f) || ["current_medication", "medication"].includes(f.category.toLowerCase()) ? factHistory(f) : null;
  const showLabel = !labelRepeatsTopic(f.label, f.category);
  const value = displayValue(f);
  const singleDate = !hist && f.date && f.sources.every((s) => s.doc_date === f.date);
  return (
    <div role="button" tabIndex={0} onMouseEnter={() => onHover(n)} onMouseLeave={() => onHover(null)} onFocus={() => onHover(n)} onBlur={() => onHover(null)}
      onClick={() => onClickRow(f)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClickRow(f); } }}
      className="group flex cursor-pointer gap-3 border-b border-l-2 border-b-border/60 border-l-transparent py-2.5 pl-2 transition-colors focus-visible:outline-2 focus-visible:outline-ring"
      style={{ borderLeftColor: lit ? c : undefined }}>
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-primary-foreground" style={{ background: c }} aria-label={`Answer ${n}`}>{n}</span>
      <div className="min-w-0 flex-1">
        {hist ? (
          <>
            <div className="text-lg font-semibold leading-snug">{f.label}</div>
            <ol className="mt-1 space-y-0.5" aria-label="History, newest first">
              {hist.map((h, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-3 text-sm">
                  <span className={i === 0 ? "font-semibold" : "text-muted-foreground"}>{h.value}</span>
                  {h.delta && <DeltaTag d={h.delta} />}
                  <SourceChip source={h.source} onOpen={onOpen} />
                </li>
              ))}
            </ol>
          </>
        ) : (
          <>
            {showLabel && <div className="text-sm text-muted-foreground">{f.label}</div>}
            <div className="text-lg font-semibold leading-snug">{value}</div>
            {f.derived && <div className="text-sm italic text-muted-foreground">Calculated from the documents</div>}
            <div className="mt-1 flex flex-wrap gap-x-3">{f.sources.map((s, i) => <SourceChip key={i} source={s} onOpen={onOpen} />)}</div>
            {!singleDate && f.date && <div className="text-xs text-muted-foreground">{formatDate(f.date)}</div>}
          </>
        )}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex h-screen items-center justify-center" role="status" aria-label="Loading">
      <div className="h-10 w-10 animate-spin rounded-full border-4 border-muted border-t-primary" />
    </div>
  );
}

function useSplit() {
  const [pct, setPct] = useState(45);
  const box = useRef<HTMLDivElement>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: PointerEvent) => {
      const r = box.current?.getBoundingClientRect();
      if (r) setPct(clampSplit(((ev.clientX - r.left) / r.width) * 100));
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); document.body.style.userSelect = ""; };
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") setPct((p) => clampSplit(p - 2));
    if (e.key === "ArrowRight") setPct((p) => clampSplit(p + 2));
  };
  return { pct, box, onPointerDown, onKeyDown };
}

export function PatientView({ patientId: PATIENT_ID, role, task }: { patientId: string; role: string; task: string }) {
  const [patient, setPatient] = useState<Patient | null>(null);
  const [docs, setDocs] = useState<DocumentFull[]>([]);
  const [cave, setCave] = useState<Passage | null | undefined>(undefined);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [hoverRow, setHoverRow] = useState<number | null>(null);
  const [activeRows, setActiveRows] = useState<number[]>([]);
  const [target, setTarget] = useState<ScrollTarget>(null);
  const cycle = useRef(new Map<string, number>());
  const reqId = useRef(0);

  useEffect(() => {
    getPatient(PATIENT_ID).then(async (p) => {
      setPatient(p);
      setDocs(await Promise.all(p.documents.map((d) => getDocument(PATIENT_ID, d.doc_id))));
    }).catch(() => setPatient(null));
    searchPassages(PATIENT_ID, "Allergie", 10).then((ps) => setCave(ps.find((p) => p.kind !== "heading") ?? null)).catch(() => setCave(null));
  }, [PATIENT_ID]);

  const submit = useCallback(async () => {
    if (!role.trim() || !task.trim()) { setSummary(null); setLoading(false); return; }
    const id = ++reqId.current;
    setLoading(true); setError(null); setSummary(null);
    try {
      const res = await getSummary(PATIENT_ID, role.trim(), task.trim());
      if (id === reqId.current) setSummary(res);
    } catch (e) {
      if (id === reqId.current) setError(e instanceof Error ? e.message : "Unknown error");
    } finally { if (id === reqId.current) setLoading(false); }
  }, [PATIENT_ID, role, task]);
  useEffect(() => { submit(); }, [submit]);

  const ranked = useMemo(() => (summary ? rankSummary(summary) : null), [summary]);
  // Row numbers follow on-screen order: priority facts by category, then the rest.
  const numbered = useMemo(() => {
    if (!ranked) return [] as Fact[];
    const inOrder = (list: Fact[]) => ranked.categories.flatMap((c) => list.filter((f) => f.category === c));
    return [...inOrder(ranked.priorityFacts), ...inOrder(ranked.otherFacts)];
  }, [ranked]);
  const rowOf = useMemo(() => new Map(numbered.map((f, i) => [f.id, i + 1])), [numbered]);
  const visibleConflicts = useMemo(() => (ranked ? [...ranked.relevantContradictions, ...ranked.otherContradictions] : []), [ranked]);

  const marks = useMemo(() => {
    const out: Mark[] = [];
    numbered.forEach((f, i) => f.sources.forEach((s) => s.spans.forEach(([a, b]) => out.push({ doc_id: s.doc_id, a, b, rows: [i + 1], cfs: [] }))));
    visibleConflicts.forEach((c, ci) => c.claims.forEach((cl) => cl.source.spans.forEach(([a, b]) => out.push({ doc_id: cl.source.doc_id, a, b, rows: [], cfs: [ci] }))));
    return out;
  }, [numbered, visibleConflicts]);

  const openSource = useCallback((s: Source) => setTarget({ doc_id: s.doc_id, a: s.spans[0]?.[0] ?? s.start, nonce: Date.now() }), []);
  const clickRow = useCallback((f: Fact) => {
    const all = f.sources.flatMap((s) => s.spans.map(([a]) => ({ doc_id: s.doc_id, a })));
    if (!all.length) return;
    const i = (cycle.current.get(f.id) ?? -1) + 1;
    cycle.current.set(f.id, i);
    const t = all[i % all.length]!;
    setTarget({ ...t, nonce: Date.now() });
  }, []);

  const latest = patient?.documents.map((d) => d.date).filter(Boolean).sort().at(-1) ?? null;
  const ctx = { rowOf, activeRows, setHoverRow, clickRow, openSource };
  const split = useSplit();

  if (!patient || (loading && !summary)) return <Spinner />;

  return (
    <div className="flex h-screen flex-col">
      <AppHeader
        left={
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
            <div className="text-lg font-semibold">{patient.name}</div>
            <div className="text-sm text-muted-foreground">ID {patient.patient_id}</div>
            <div className="text-sm text-muted-foreground">{patient.documents.length} documents</div>
            {latest && <div className="text-sm text-muted-foreground">Latest: {formatDate(latest)}</div>}
            {USE_MOCK && summary?.mock && <span className="rounded-sm border bg-muted px-1.5 py-0.5 text-xs font-semibold tracking-wide text-muted-foreground">MOCK DATA</span>}
          </div>
        }
        right={<Button size="sm" asChild><Link to="/patients">Finish assessment</Link></Button>}
      />

      <div ref={split.box} className="flex min-h-0 flex-1 max-[999px]:flex-col" style={{ ["--split" as string]: `${split.pct}%` }}>
        <main className="min-w-0 overflow-auto bg-card px-5 py-4 max-[999px]:max-h-[50%] min-[1000px]:shrink-0 min-[1000px]:basis-[var(--split)]">
          <TaskBar patientId={PATIENT_ID} role={role} task={task} />
          <div className="mt-3">
          {cave === undefined ? null : cave ? (
            <div className="flex flex-wrap items-center gap-2 rounded-sm border border-cave bg-cave-bg px-3 py-2 text-cave">
              <span className="font-bold">CAVE</span>
              <span className="font-medium">{cave.text.trim()}</span>
              <SourceChip source={passageToSource(cave, patient)} onOpen={openSource} />
            </div>
          ) : (
            <div className="rounded-sm border border-dashed border-missing-border bg-missing-bg px-3 py-2 text-missing">Allergies: not documented</div>
          )}
          </div>
          <section className="mt-4" aria-live="polite">
            {!task.trim() && <div className="py-10 text-center text-muted-foreground">Select or enter a task to see the relevant patient facts.</div>}
            {error && !loading && (
              <div role="alert" className="flex items-center justify-between gap-3 rounded-md border border-destructive bg-cave-bg p-3 text-destructive">
                <span>Could not load the summary: {error}</span>
                <Button variant="destructive" type="button" onClick={() => submit()}>Retry</Button>
              </div>
            )}
            {summary && !loading && <AnalysisNotices s={summary} />}
            {summary && ranked && !loading && <Results s={summary} ranked={ranked} ctx={ctx} />}
          </section>
        </main>
        <div role="separator" aria-orientation="vertical" aria-label="Resize panels" aria-valuemin={20} aria-valuemax={70} aria-valuenow={Math.round(split.pct)} tabIndex={0}
          onPointerDown={split.onPointerDown} onKeyDown={split.onKeyDown}
          className="w-1.5 shrink-0 cursor-col-resize touch-none bg-border transition-colors hover:bg-primary focus-visible:bg-primary max-[999px]:hidden" />
        <aside aria-label="Original documents" className="min-h-0 min-w-0 flex-1 max-[999px]:border-t">
          <PageStack docs={docs} marks={marks} hoverRow={hoverRow} target={target} onActiveRows={setActiveRows} />
        </aside>
      </div>
    </div>
  );
}

type Ctx = { rowOf: Map<string, number>; activeRows: number[]; setHoverRow: (n: number | null) => void; clickRow: (f: Fact) => void; openSource: (s: Source) => void };

function Results({ s, ranked, ctx }: { s: SummaryResponse; ranked: ReturnType<typeof rankSummary>; ctx: Ctx }) {
  const allConflicts = [...ranked.relevantContradictions, ...ranked.otherContradictions];
  const cats = [...new Set([...ranked.categories, ...allConflicts.map((c) => c.category)])];
  const renderCategory = (c: string, facts: Fact[], missing: Missing[], conflicts: Contradiction[]) => (
    <section key={c}>
      <h3 className="mb-1.5 flex items-center gap-2 text-lg font-semibold text-foreground">
        {categoryTitle(c)}
        {conflicts.length > 0 && <span className="flex h-5 w-5 items-center justify-center rounded-full bg-conflict-bg text-xs font-bold text-conflict" aria-label="Has conflicting information">!</span>}
      </h3>
      <div className="space-y-1">
        {conflicts.map((x) => <ConflictLine key={x.id} c={x} onOpen={ctx.openSource} />)}
        {facts.map((f) => { const n = ctx.rowOf.get(f.id)!; return <FactRow key={f.id} f={f} n={n} lit={ctx.activeRows.includes(n)} onHover={ctx.setHoverRow} onClickRow={ctx.clickRow} onOpen={ctx.openSource} />; })}
        {missing.map((m, i) => <MissingLine key={i} m={m} />)}
      </div>
    </section>
  );
  const group = (factsL: Fact[], missingL: Missing[], conflictsL: Contradiction[]) => cats.map((c) => {
    const f = factsL.filter((x) => x.category === c), m = missingL.filter((x) => x.category === c), k = conflictsL.filter((x) => x.category === c);
    return f.length || m.length || k.length ? renderCategory(c, f, m, k) : null;
  });
  const hasOther = ranked.otherFacts.length + ranked.otherMissing.length + ranked.otherContradictions.length > 0;
  return (
    <div className="space-y-6">
      {s.facts.length === 0 && <div>No verified facts were found for this task.</div>}
      <section aria-label="Overview" className="space-y-5">
        <h2 className="border-b-2 border-primary pb-1.5 text-3xl font-semibold tracking-tight">Overview</h2>
        {group(ranked.priorityFacts, ranked.priorityMissing, ranked.relevantContradictions)}
        {ranked.priorityFacts.length === 0 && ranked.priorityMissing.length === 0 && ranked.relevantContradictions.length === 0 && <div className="text-muted-foreground">No task-specific information returned.</div>}
      </section>
      {hasOther && (
        <section aria-label="Other patient information" className="space-y-5">
          <h2 className="border-b pb-1.5 text-3xl font-semibold tracking-tight">Other patient information</h2>
          {group(ranked.otherFacts, ranked.otherMissing, ranked.otherContradictions)}
        </section>
      )}
    </div>
  );
}

/** Task entry above the facts. PROTOTYPE: preset options come from QUICK and will be replaced by the backend orchestrator (see README). */
function TaskBar({ patientId, role, task }: { patientId: string; role: string; task: string }) {
  const navigate = useNavigate();
  const [val, setVal] = useState(task);
  useEffect(() => setVal(task), [task]);
  const go = (t: string) => navigate({ to: "/patients/$patientId", params: { patientId }, search: { task: t.trim() } });
  const options = [...QUICK.filter((q) => q.role === role), ...QUICK.filter((q) => q.role !== role)];
  return (
    <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (val.trim()) go(val); }}>
      <label htmlFor="task-input" className="sr-only">Task</label>
      <input id="task-input" list="task-options" value={val} autoFocus={!task}
        onChange={(e) => { setVal(e.target.value); if (options.some((o) => o.task === e.target.value)) go(e.target.value); }}
        placeholder={`Task (${role}) – type or pick from the list`}
        className="min-w-0 flex-1 rounded-sm border border-input bg-card px-2.5 py-1.5 text-sm" />
      <datalist id="task-options">{options.map((o) => <option key={o.task} value={o.task} />)}</datalist>
      <Button type="submit" size="sm" disabled={!val.trim() || val.trim() === task.trim()}>Show</Button>
    </form>
  );
}
