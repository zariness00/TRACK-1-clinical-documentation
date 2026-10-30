import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppHeader } from "@/components/clinical/AppHeader";
import { listPatients } from "@/lib/api";
import { formatDate } from "@/lib/categories";
import { getSession } from "@/lib/session";
import type { Patient } from "@/lib/types";

export const Route = createFileRoute("/patients/")({
  ssr: false,
  beforeLoad: () => { if (!getSession()) throw redirect({ to: "/" }); },
  head: () => ({
    meta: [
      { title: "Select patient · Task-focused patient facts" },
      { name: "description", content: "Choose the patient you are about to assess." },
      { property: "og:title", content: "Select patient" },
      { property: "og:description", content: "Choose the patient you are about to assess." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Patients,
});

function Patients() {
  const [list, setList] = useState<Patient[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { listPatients().then(setList).catch((e) => setErr(String(e?.message ?? e))); }, []);
  return (
    <div className="min-h-screen">
      <AppHeader />
      <main className="mx-auto mt-10 max-w-2xl px-5">
        <h1 className="mb-4 text-xl font-semibold">Select a patient</h1>
        {err && <div role="alert" className="text-destructive">Could not load patients: {err}</div>}
        {!list && !err && <div className="text-muted-foreground">Loading patients…</div>}
        <ul className="space-y-2">
          {list?.map((p) => {
            const latest = p.documents.map((d) => d.date).filter(Boolean).sort().at(-1) ?? null;
            return (
              <li key={p.patient_id}>
                <Link to="/patients/$patientId" params={{ patientId: p.patient_id }} search={{ task: "" }} className="flex items-center justify-between rounded-md border bg-card p-4 hover:border-primary">
                  <div>
                    <div className="font-semibold">{p.name}</div>
                    <div className="text-sm text-muted-foreground">ID {p.patient_id}</div>
                  </div>
                  <div className="text-right text-sm text-muted-foreground">
                    <div>{p.documents.length} documents</div>
                    {latest && <div>Latest: {formatDate(latest)}</div>}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      </main>
    </div>
  );
}
