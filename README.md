# Clinical Documentation: one verified screen per role and task

A clinician enters their role and task in free text (*"You are the anaesthetist, pre-op check before a
quadrantectomy"*) and gets **one screen** with exactly the patient facts that task needs:

- **Every fact is linked** to the sentence or table row it comes from; one click highlights the source.
- **"Not documented"** when the task needs something the eight documents do not contain.
- **"Analysis incomplete"** when the AI could not finish a check, so it is never mistaken for absence.
- **Contradictions** between documents, each linking all of its passages.
- **Nothing unverified reaches the screen.** The AI only *proposes*; deterministic code checks every quote,
  number, side (left/right), negation and certainty against the source before display.

Task brief from the organisers: [`docs/TRACK_BRIEF.md`](docs/TRACK_BRIEF.md).

## Results on the 5 evaluation situations

| Metric | Result | Target |
|---|---|---|
| Correct answers | **15 / 15** | 15 / 15 |
| Traceability (displayed facts whose link shows the right passage) | **100 %** (40 / 40; 173 / 173 cited sources verified) | 100 % |
| Invented facts | **0** (unsupported claims are blocked by the validator) | 0 |
| Contradictions (real minus false alarms) | **6 real, 0 false alarms** | 5 or more |
| Load time, slowest of 5 | **6.1 s** (range 3.0–6.1 s) | < 10 s |

Measured with `scripts/bench_models.py --app` on the latest version. Answers are scored with a keyword rubric
built from `cases/expected.json`; runs over the last day gave 15/15, 15/15 and 14/15 (the miss was a provider
time-out, shown as "analysis incomplete", not as a wrong answer). Citations were 100 % and contradictions 6/0 in
every run. Manual review in the UI: see [`TESTING.md`](TESTING.md).

Contradictions found: two BMIs that do not match height and weight in the same letter (2015, 2016), two
admission dates for the April 2016 stay, carotid stenosis recorded as right in one passage and left everywhere
else, enlarged axillary lymph nodes on palpation vs none on ultrasound, and a biopsy date mismatch. The AI also
flags that the April 2016 letter carries the 2015 case number (counted conservatively, not in the 6).

## Architecture

```
  START-UP (once)                                   PER REQUEST  POST /api/summary   (< 10 s)
  ┌────────────────────────────────────┐            ┌──────────────────────────────────────────────────────────┐
  │ corpus.py   load the 8 documents   │            │ situation text ("You are the anaesthetist ...")          │
  │ segment.py  split into passages    │            │      │                                                   │
  │             ID 12340018#p54 +      │            │      ▼                                                   │
  │             exact char offsets     │            │ 1 PLANNER   which facts does this role/task need?        │
  │                                    │            │             known role: priority checklist (no LLM call) │
  │ contradictions.py  5 rule checks ──┼──┐         │             unseen role: LLM adds task-specific needs    │
  │ ai.py  AI contradiction worker   ──┼──┤         │      │                                                   │
  │        + semantic self-check,      │  │         │      ▼                                                   │
  │        cached per patient          │  │         │ 2 TWO WORKERS in parallel, streamed                      │
  └────────────────────────────────────┘  │         │   summary:      facts + verbatim German quotes           │
                                          │         │   missing-info: is each need documented? yes / no        │
                                          │         │      │                                                   │
                                          │         │      ▼                                                   │
                                          │         │ 3 RECONCILE  merge, de-duplicate, evidence wins,         │
                                          │         │              keep only the quote-supported part          │
                                          │         │      │                                                   │
                                          │         │      ▼                                                   │
                                          └────────►│ 4 VALIDATE   quote exists? numbers, side, polarity,      │
                                                    │              negation, certainty? + cached contradictions│
                                                    │      │                                                   │
                                                    │      ▼                                                   │
                                                    │ UI: re-verifies every quote against the document text,   │
                                                    │     renders sections, highlights sources on click        │
                                                    └──────────────────────────────────────────────────────────┘
```

### Components

| Component | Files | What it does |
|---|---|---|
| Document layer | `backend/corpus.py`, `segment.py`, `context.py` | Loads the 8 documents, splits them into citable passages (one per sentence / table row, German abbreviations and tables handled) with stable IDs and exact offsets, renders a prompt with passage IDs. |
| Quote verification | `backend/verify.py` | Finds a quote in a document: exact, then normalised (whitespace, quotes, dashes, hyphenation), then case-insensitive; auto-corrects citations that point to the wrong document. |
| Validator | `backend/validate.py` | Runs on every response: quote must exist; every number in a value must be in its quote; left/right, positive/negative, negation and "suspected" (Vd.a.) must match; "not documented" claims are checked against all documents. |
| Rule-based contradictions | `backend/contradictions.py` | BMI vs height/weight in the same letter, two dates for one event, left vs right for the same finding, present vs absent, procedure vs sampling dates. Conservative: no measurement noise, no changes over time. |
| AI orchestration | `backend/ai.py` | Hybrid planner (role checklists + LLM for unseen roles), summary and missing-info workers in parallel, patient-wide contradiction worker (cached), reconciliation, 9 s budget with streaming so a slow model degrades to a partial screen instead of an empty one. |
| API | `backend/api.py`, `models.py` | FastAPI. `POST /api/summary` (main), documents and passages for the source viewer, `/api/health`. Falls back to a keyword mock without an API key. |
| Verified fact pool | `backend/ibm_pool.py` | IBM's extraction re-linked to document passages (documents win where they differ); `GET /api/patients/{pid}/facts`. |
| Frontend | `frontend/` (React, TanStack, Tailwind, built with Lovable) | Role sign-in, free-text task, sections in priority order, source viewer with highlighting. Re-verifies every quote client-side (`src/lib/verify.ts`) before rendering. |

### How the AI is kept honest

1. **Quotes, not claims.** Workers must return each fact with `doc_id`, `passage_id` and a verbatim German quote.
   Model-invented passage IDs are replaced by the real passage the quote is found in.
2. **Guards before display.** Numbers, laterality, polarity, negation and certainty of the value must be backed by
   the quote. If the model adds unsupported context ("in 06/2015", "(60% left)"), only that annotation is dropped;
   a conflicting dose, side or date rejects the whole fact (`tests/test_ai_trim.py`).
3. **Absence needs a verdict.** "Not documented" only from an explicit check over all documents, and dropped if
   the term is found anywhere. Needs the AI did not finish are listed as "analysis incomplete".
4. **Contradictions need two verified passages** from different places, plus a one-time semantic check.

### Models and services

| Role | Model | Settings |
|---|---|---|
| Planner (unseen roles only), summary worker, missing-info worker | **DeepSeek V4 Flash** (`deepseek-ai/DeepSeek-V4-Flash-0731`) | reasoning off, JSON schema output, streamed, 9 s budget |
| Contradiction worker + semantic check | **gpt-oss-120b** (`openai/gpt-oss-120b`) and DeepSeek V4 Flash | medium reasoning; runs once at start-up, cached |
| Provider | **Nebius Token Factory** (OpenAI-compatible API) | |

Chosen after benchmarking Qwen3.5-397B, DeepSeek V4 Flash and gpt-oss-120b on the real documents
(`scripts/bench_models.py`): DeepSeek was the only one under 10 s end to end; gpt-oss found the most real
contradictions; Qwen3.5 was accurate but 21–26 s. All models, reasoning levels and time-outs are configurable via
environment variables (`LLM_MODEL_*`, `LLM_REASONING_*`, `LLM_BUDGET_S`).

## Run it

The `data` folder is not part of this repository. Clone the track repository and point `DATA_DIR` at its
`data` folder.

**Backend** (Python 3.9+):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cat > .env <<'EOF'
NEBIUS_API_KEY=<your Nebius Token Factory key>
DATA_DIR=/absolute/path/to/TRACK-1-clinical-documentation/data
EOF
.venv/bin/uvicorn backend.api:app --port 8000
```

Start-up takes about 10–15 s (contradictions are computed before the server accepts requests).
Check http://localhost:8000/api/health (`"summarizer": "ai"`, contradictions `"ready"`); API docs at `/docs`.
Without `NEBIUS_API_KEY` the app runs on a keyword mock.

**Frontend** (Node.js):

```bash
cd frontend
printf 'VITE_USE_MOCK=false\nVITE_API_BASE=http://127.0.0.1:8000/api\n' > .env.local
npm install
npm run dev
```

Open the address it prints (e.g. http://localhost:8080).

**Tests and evaluation:**

```bash
.venv/bin/python -m pytest -q tests                 # unit tests, no key needed
.venv/bin/python scripts/bench_models.py --app      # the 5 situations end to end
.venv/bin/python scripts/review.py                  # side-by-side view for manual review (server running)
```

Full testing guide: [`TESTING.md`](TESTING.md).

## What works, what does not (yet)

**Works**
- All 5 situations answered with linked sources; new roles (pharmacist, physiotherapist, radiotherapist)
  get their own task-specific screens through the LLM planner.
- Strict evidence: no fact without a verified quote; wrong doses, sides and dates are blocked.
- 6 real contradictions, no false alarms; under 10 s per request.
- Graceful degradation: if a model call fails or is slow, the screen shows what was verified and marks the
  rest "analysis incomplete" instead of guessing.

**Limitations**
- Some values are still shown in German (e.g. "KM-Allergie" = contrast media allergy).
- Long list facts (all diagnoses, all medication) can contain items that are not individually linked.
- Latency depends on the provider: 3–6 s normally, close to 9 s when Nebius is slow.
- Answer scoring is automated with a keyword rubric; the jury's judgement may differ.

## Repository

```
backend/      FastAPI app, documents, verification, validator, contradictions, AI orchestration
frontend/     React UI (Lovable)
scripts/      bench_models.py (evaluation), review.py (manual review), inspect_data.py, compare_ibm.py
tests/        unit tests on synthetic fixtures (no real data)
cases/        the 5 situations and expected answers
docs/         track brief, integration notes
```

Only synthetic data is used. IBM's data stays out of this repository.

