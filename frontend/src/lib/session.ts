import { useEffect, useState } from "react";

export type Session = { role: string; name: string };
const KEY = "clinical-session";

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const s = JSON.parse(window.sessionStorage.getItem(KEY) ?? "null");
    return s && typeof s.role === "string" && s.role.trim() ? s : null;
  } catch { return null; }
}
export function setSession(s: Session) {
  window.sessionStorage.setItem(KEY, JSON.stringify({ role: s.role.trim(), name: s.name.trim() }));
}
export function clearSession() { window.sessionStorage.removeItem(KEY); }

export function useSession() {
  const [s, setS] = useState<Session | null>(null);
  useEffect(() => setS(getSession()), []);
  return s;
}

export const ROLES = ["Anaesthetist", "Surgeon", "General practitioner", "Emergency physician", "Oncologist", "Cardiologist", "Neurologist", "Radiologist", "Nurse"];
export const QUICK = [
  { role: "Anaesthetist", task: "Pre-operative anaesthesia check: quadrantectomy with intraoperative radiotherapy on 16 May 2016" },
  { role: "Surgeon", task: "Surgery planning after the tumour board, April 2016" },
  { role: "General practitioner", task: "Diabetes review in spring 2016" },
  { role: "Emergency physician", task: "Night duty: arrives at 02:00 with dizziness and blurred vision in the left eye" },
  { role: "General practitioner", task: "Check which follow-ups from her June 2015 hospital stay are still open" },
];
/** Quick-fill tasks for this role first, then the rest. */
export function quickFor(role: string) {
  const r = role.toLowerCase();
  return [...QUICK.filter((q) => q.role.toLowerCase() === r), ...QUICK.filter((q) => q.role.toLowerCase() !== r)];
}
