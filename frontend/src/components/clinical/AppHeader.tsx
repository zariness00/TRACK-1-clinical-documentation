import type { ReactNode } from "react";
import { useNavigate } from "@tanstack/react-router";
import { clearSession, useSession } from "@/lib/session";

export function AppHeader({ left, right }: { left?: ReactNode; right?: ReactNode }) {
  const s = useSession();
  const navigate = useNavigate();
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 border-b bg-card px-5 py-2">
      <div className="min-w-0">{left ?? <div className="font-semibold">Task-focused patient facts</div>}</div>
      <div className="ml-auto flex flex-wrap items-center gap-4">
        {right}
        {s && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{s.name ? `${s.name} · ` : ""}{s.role}</span>
            <button type="button" onClick={() => { clearSession(); navigate({ to: "/", replace: true }); }} className="underline underline-offset-2 hover:text-foreground">Sign out</button>
          </div>
        )}
      </div>
    </div>
  );
}
