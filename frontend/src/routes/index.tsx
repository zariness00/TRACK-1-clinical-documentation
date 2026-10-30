import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { AppHeader } from "@/components/clinical/AppHeader";
import { getSession, ROLES, setSession } from "@/lib/session";

export const Route = createFileRoute("/")({
  ssr: false,
  beforeLoad: () => { if (getSession()) throw redirect({ to: "/patients" }); },
  head: () => ({
    meta: [
      { title: "Sign in · Task-focused patient facts" },
      { name: "description", content: "Enter your profession to start reviewing task-relevant patient facts." },
      { property: "og:title", content: "Sign in · Task-focused patient facts" },
      { property: "og:description", content: "Clinical decision-support prototype: every fact linked to its source passage." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Login,
});

function Login() {
  const [role, setRole] = useState("");
  const [name, setName] = useState("");
  const navigate = useNavigate();
  return (
    <div className="min-h-screen">
      <AppHeader />
      <form
        className="mx-auto mt-16 max-w-md space-y-4 rounded-md border bg-card p-6"
        onSubmit={(e) => { e.preventDefault(); if (!role.trim()) return; setSession({ role, name }); navigate({ to: "/patients" }); }}
      >
        <h1 className="text-xl font-semibold">Sign in</h1>
        <div>
          <label htmlFor="role" className="mb-1 block text-sm font-medium">Profession</label>
          <input id="role" list="roles" value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. Anaesthetist" autoFocus className="w-full rounded-sm border border-input bg-card px-2.5 py-1.5" />
          <datalist id="roles">{ROLES.map((r) => <option key={r} value={r} />)}</datalist>
        </div>
        <div>
          <label htmlFor="name" className="mb-1 block text-sm font-medium">Name <span className="text-muted-foreground">(optional)</span></label>
          <input id="name" value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded-sm border border-input bg-card px-2.5 py-1.5" />
        </div>
        <button type="submit" disabled={!role.trim()} className="w-full rounded-sm bg-primary px-4 py-2 font-medium text-primary-foreground disabled:opacity-50">Continue</button>
      </form>
    </div>
  );
}
