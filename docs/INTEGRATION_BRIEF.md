# Integration brief — Track 1 clinical documentation

## Goal (from the brief)
A clinician enters a situation (role + task, written in English) and gets ONE screen with the patient facts that
task needs, for synthetic patient Anna Musterfrau (8 German documents, 2015–2016).
- Every fact links to the sentence or table row it comes from.
- If the task needs something the documents do not contain, the screen says "not documented".
- The app points out contradictions in the documents.
- Only facts from the documents (translations, generic drug names, calculated values such as a weight change are fine).
- Present-tense questions mean "as of 15.04.2016" (latest document): show the latest value and its date.

## Metrics (5 test situations in `cases/situations.md`, answers in `cases/expected.json`; the jury adds new situations)
| Metric | Target | Notes |
|---|---|---|
| Correct answers | 15/15 | Visible **without clicking** (scrolling is fine), with a link to a source document |
| Traceability | 100% | Every link shows the right passage |
| Invented facts | 0 | Guarded by `backend/validate.py` |
| Contradictions | ≥ 5, minus false alarms | `backend/contradictions.py` finds 6 with 0 false alarms |
| Load time | < 10 s | Slowest of the 5, submit → complete screen |

Judging: clinical approach, technical quality & AI, execution (works end to end on the 5 and on new situations), 1/3 each.

## Architecture (who owns what)
| Part | Files | What it does |
|---|---|---|
| Documents & backend (Veronika) | `backend/corpus.py`, `segment.py`, `verify.py`, `validate.py`, `api.py`, `models.py` | Loads the JSON documents (`<BR>` → `\n`), splits them into passages `<doc_id>#p<n>` with exact offsets, verifies quotes, runs the safety checks, serves the API |
| Contradictions (rules) | `backend/contradictions.py` | 5 conservative rules at start-up: BMI vs height/weight, two admission/discharge dates in one letter, left vs right for the same finding, present vs absent, biopsy date across documents |
| IBM fact pool | `backend/ibm_pool.py` | IBM's extraction used as candidates only: each entry linked to its passage, status re-read from the document, allergy from the documents |
| AI orchestration (Zarina) | `backend/ai.py` (Nebius, key `NEBIUS_API_KEY` in `.env`) | `summarize(patient, role, task, context)` → `CandidateOutput`; checklist needs per role/task, LLM planning only for unfamiliar cases, AI contradictions warmed up at start-up; `not_checked` = needs the AI could not finish ("analysis incomplete", never shown as "not documented") |
| UI (Gregor) | frontend | Renders `SummaryResponse`; see UI spec below |
| QA (Polina) | `cases/`, metric script | Checks the 15 answers, links, timings |

## Contract (`backend/models.py`)
- AI returns `CandidateOutput`: facts `{category, label, value (English), date?, derived, sources:[{doc_id, passage_id?, quote (verbatim German)}]}`,
  `not_documented:[{category, item, searched[]}]`, `contradictions:[{summary, claims:[{statement, source}]}]`.
- `POST /api/summary {role, task}` (or `{situation}`) → `SummaryResponse`: verified `facts[]` (each source has `doc_id, quote, start, end, spans, passage_ids`),
  `not_documented[]`, `contradictions[]` (rule-based ones always included, `origin:"rules"`), `rejected[]` (what the validator blocked, with reasons), `timings_ms`.
- Highlight a source: `GET /api/patients/{pid}/documents/{doc_id}` → `text`; highlight `text[start:end]` (or each pair in `spans`). Never use the raw JSON text with `<BR>`.
- A calculated value must carry `derived: true`, otherwise the number check rejects it.

## UI spec (decided by the team; the frontend in this repo is the reference implementation)
- **Header:** patient banner (name, sex, DOB, patient ID, last document date) with the allergy always visible in red ("Allergy: contrast media (KM)").
- **Input:** role + task (planned: one free-text "Describe the situation" box with role chips and the 5 examples as presets).
- **Left — answer-first checklist:** one row per question the task raises: item · finding (English, value first) · date tag · source chips (document type + date).
  Row states: ✓ documented, – not documented (amber, with what was searched), ! conflicting sources. Task-specific risk chips above (e.g. antiplatelet, contrast allergy, GLP-1 agonist).
  Below: all contradictions on every screen (task-relevant first, nothing collapsed), then a medication table (active substance, brand, dose, M-N-E-Night schedule) where relevant.
- **Right — source documents as a scrollable page stack:** all 8 documents as A4-style pages (letterhead from metadata, serif text, `white-space: pre-wrap`).
  Order: pages with answers (by date) → pages with only conflicting passages → the rest (faded). No tabs; reading needs no clicks.
- **Colour + number linking:** each answer row gets a number and a colour (palette of 12). Its passages on the right are highlighted in that colour (fill ~28% + underline) with the same number badge at the start.
  Conflicting passages: red "!" badge + red striped/dashed marking. A passage used by several rows shows all numbers.
- **Book-like page strip** at the bottom: thumbnails with coloured marks where highlights are, current page outlined, "Page n / N", click to jump.
- **Two-way link:** hover a row → its passages pulse; scrolling the pages lights up the row of the passage in view; clicking a row/chip (optional) scrolls to that passage, repeated clicks cycle.
- Language: English only on the left; source pages show the original German.

## Integration tasks (suggested order)
1. Follow `TESTING.md`: `.env` with `NEBIUS_API_KEY` + `DATA_DIR`, `pytest`, start the API; `/api/health` must say `"summarizer": "ai"` and `"ai_contradictions": "ready"`.
2. Run the 5 situations: `scripts/bench_models.py --app` (latency, answers, rejected facts) and `scripts/review.py` (incl. new situations); check each answer in `cases/expected.json` is visible, linked and correct; record `timings_ms.total` (< 10 s).
3. Wire the team's frontend to `/api/summary` + `/documents/{doc_id}` so it follows the UI spec above. Keep its existing design; only connect data and fill gaps against the spec.
4. Fix gaps the eval shows in the AI prompt (selection, wording, `derived`), never by loosening the validator.
5. README for the submission: architecture (components, how they connect, models/services), how to run, metric results. Repo public, no data.
