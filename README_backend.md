# Backend (Task 1 — documents, passages, quote verification, API)

## Run
```bash
pip install -r requirements.txt
# data/ is git-ignored. Put it at ./data or point DATA_DIR at it:
export DATA_DIR=/path/to/TRACK-1-clinical-documentation/data
python scripts/inspect_data.py          # check the 8 documents split well
python -m pytest -q tests               # 14 tests on synthetic fixtures (no real data needed)
uvicorn backend.api:app --reload --port 8000   # http://localhost:8000/docs
```

## What it does
1. **Ingestion (start-up, cached in memory).** `corpus.py` loads `data/patient-*/documents/*.json` (Track-1 format: `metadata` + `text` with `<BR>` breaks; `doc_id` = `metadata.id`, title/date/type/department/admission from metadata; also txt/html/pdf) and the IBM extraction `*.json` files next to `documents/`. The IBM files are only hints: where they differ from the documents, the documents win.
2. **Segmentation.** `segment.py` splits each document into citable **passages**: one per line (each diagnosis, `Größe: 170 cm` field and medication row is its own passage), with prose that wraps mid-sentence re-joined and multi-sentence lines split into sentences. Each passage has an ID of the form `<doc_id>#p<n>` and exact raw offsets, so `text[start:end] == passage.text` always holds. It handles German abbreviations (Dr., z. B., Z. n., geb., Mon.), date and ordinal dots, and tab/pipe/space-aligned tables.
3. **Quote verification.** `verify.py` matches a quote at one of these levels: `exact` → `normalized` (whitespace, „typographic“ quotes, dashes, soft hyphens, words hyphenated across a line break) → `caseless`. If the quote is found in a different document from the one cited, the status is `wrong_doc` and the citation is auto-corrected. Otherwise the quote is **rejected**. Ellipsis quotes (`A ... B`) are supported. Quotes shorter than 5 characters are rejected as ambiguous (so `BMI: 29` is still citable).
4. **Validation guards.** `validate.py` checks every model claim before the UI sees it:
   - `quote`: the quote must be found.
   - `numbers`: every number in the displayed value must appear in a quote. This catches wrong doses and dates. Values marked `derived` (calculated) are exempt.
   - `laterality`: left/right in the value must match links/rechts in the source.
   - Contradictions need at least 2 verified passages at different places.

   Anything that fails goes to `rejected[]` with a reason, which is useful for metrics.

## Contract (`backend/models.py`)
- **AI (Zarina):** create `backend/ai.py` with `summarize(patient, role, task, context: str) -> CandidateOutput | dict`. `context` is all documents with passage IDs (`GET /context`). Each source cites `{doc_id, passage_id?, quote}`, with the quote copied verbatim in German. While `backend/ai.py` doesn't exist (or with `MOCK=1`), `/api/summary` uses the keyword mock, which runs through the same validator.
- **UI (Gregor):** render `SummaryResponse` only. To open a source, call `GET /documents/{doc_id}` and highlight `text[start:end]` (or each pair in `spans`).

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | data dir, doc counts, mock vs ai |
| `GET /api/patients`, `GET /api/patients/{pid}` | patient picker, document list |
| `GET /api/patients/{pid}/documents/{doc_id}` | raw text and passages (source viewer) |
| `GET /api/patients/{pid}/passages?q=&doc_id=&k=` | keyword search (passage index) |
| `GET /api/patients/{pid}/context` | prompt-ready documents with passage IDs |
| `GET /api/patients/{pid}/extractions` | IBM JSON (hints) |
| `POST /api/verify` | `{doc_id, quote}` → match status and offsets |
| `POST /api/validate` | raw model JSON → what survives, and why the rest was rejected |
| `POST /api/summary` | `{role, task}` or `{situation}` → verified screen, with `timings_ms` |

## Measured on the real 8 documents
345 passages, 0 suspicious splits, 345/345 passages verify as quotes, validation < 1 ms, mock `/api/summary` < 40 ms end to end.
