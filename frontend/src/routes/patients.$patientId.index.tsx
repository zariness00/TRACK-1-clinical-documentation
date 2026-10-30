import { createFileRoute, redirect } from "@tanstack/react-router";
import { PatientView } from "@/components/clinical/ClinicalApp";
import { getSession, useSession } from "@/lib/session";

export const Route = createFileRoute("/patients/$patientId/")({
  ssr: false,
  validateSearch: (s: Record<string, unknown>) => ({ task: typeof s["task"] === "string" ? s["task"] : "" }),
  beforeLoad: () => {
    if (!getSession()) throw redirect({ to: "/" });
  },
  head: () => ({
    meta: [
      { title: "Patient view · Task-focused patient facts" },
      { name: "description", content: "Task-relevant patient facts linked to their source passages." },
      { property: "og:title", content: "Patient view" },
      { property: "og:description", content: "Every fact linked to the exact passage in the original document." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Page,
});

function Page() {
  const { patientId } = Route.useParams();
  const { task } = Route.useSearch();
  const s = useSession();
  if (!s) return null;
  return <PatientView patientId={patientId} role={s.role} task={task} />;
}
