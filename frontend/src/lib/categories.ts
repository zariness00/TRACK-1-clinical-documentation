import type { Contradiction, Missing } from "./types";

/** Edit this map to override section titles. */
export const CATEGORY_TITLES: Record<string, string> = {
  current_medication: "Current medication",
};

export function categoryTitle(c: string): string {
  if (CATEGORY_TITLES[c]) return CATEGORY_TITLES[c];
  const s = c.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function splitMissing(categories: string[], items: Missing[]) {
  const set = new Set(categories);
  return { inSections: items.filter((m) => set.has(m.category)), leftover: items.filter((m) => !set.has(m.category)) };
}

export function splitContradictions(categories: string[], items: Contradiction[]) {
  const set = new Set(categories);
  return { relevant: items.filter((c) => set.has(c.category)), other: items.filter((c) => !set.has(c.category)) };
}

export function formatDate(d: string | null): string {
  if (!d) return "";
  const t = new Date(d);
  if (isNaN(t.getTime())) return d;
  return t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
