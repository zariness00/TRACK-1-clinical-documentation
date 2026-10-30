# Clinical decision-support prototype (frontend only)

Build exactly the single screen described in the uploaded brief, with mock data on by default.

## What gets built
- Patient banner (sticky): name, ID, document count, latest document date, CAVE allergy line (red, with source chip) or grey "Allergies: not documented", "MOCK DATA" badge.
- Situation input: searchable role combobox (9 specialties + free text), task field (Enter submits), 5 quick-fill buttons with the exact texts from the brief.
- Results column (~55%): one section per category in API order, fact cards (label, value, date, source chips, "Calculated from the documents" when derived), grey dashed not-documented cards with "Searched for: ...", leftover not-documented section.
- Contradictions: amber cards with claims and chips; unrelated ones behind "Show N more not related to this task"; "None relevant to this task" fallback.
- Source panel (~45%, sticky): empty state, document metadata, full monospace text with yellow highlights for every span, scroll inside panel only, active chip state, close via X/Escape; below 1000 px slides over bottom half.
- States: loading skeleton with "Searching all 8 documents…" (form stays usable), red error with Retry (stale results cleared), empty-result message.

## Visual style
Calm clinical light theme: neutral greys, one blue accent; semantic tokens for CAVE red, contradiction amber, missing grey, highlight yellow. No gradients, no emoji, 14 px+ body text, visible focus rings.

## Technical details
- `src/lib/types.ts` – types verbatim from brief.
- `src/lib/api.ts` – `USE_MOCK = true`, `API_BASE` from `VITE_API_BASE`; getSummary, getPatient, getDocument, searchPassages (mock adds a short delay).
- `src/lib/mock.ts` – fictional "Test Patient" 0000-0001, two invented German documents, SummaryResponse (3 categories, 5 facts, 2 missing, 2 contradictions), offsets computed with indexOf.
- `src/lib/categories.ts` – editable `CATEGORY_TITLES` map + formatter.
- Components under `src/components/clinical/`; page in `src/routes/index.tsx` with proper head metadata. `rejected` / `timings_ms` never rendered.
- Small vitest checks: mock spans match quotes; category order preserved; not-documented items routed correctly.
