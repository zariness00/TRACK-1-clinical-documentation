# Testing guide

How to run the app and test it against the hackathon metrics. Everything below runs locally.

## 1. One-time setup

```bash
git checkout main && git pull origin main
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**Patient data** is not in this repo (IBM's data must stay out of git). Clone the track repo next to this one
and point `DATA_DIR` at its `data` folder, e.g. `~/TRACK-1-clinical-documentation/data`.

**API key (required for the AI pipeline).** Create a file `.env` in the repo root (it is git-ignored):

```
NEBIUS_API_KEY=<the team's Nebius key>
DATA_DIR=/absolute/path/to/TRACK-1-clinical-documentation/data
```

- Ask Zarina for the Nebius key. Never commit it, paste it in chat/Discord channels, or put it in screenshots.
- Without `NEBIUS_API_KEY` the app still runs, but `/api/summary` uses the **keyword mock**, not the AI
  (`"mock": true` in the response). Results from the mock don't count.
- OpenRouter (organisers' key) is **not used yet**: the key currently returns `401 User not found`.

## 2. Start the server

```bash
.venv/bin/uvicorn backend.api:app --port 8000
```

- Startup takes ~10-15 s: contradictions are computed **before** the server accepts requests.
- Wait for `Application startup complete`. If you see `address already in use`, another server is still running
  on port 8000: stop it first (`lsof -i :8000`, then `kill <pid>`).
- Check http://localhost:8000/api/health:
  - `"summarizer": "ai"` (not `"mock"`)
  - `"ai_contradictions": {"0000-0001": "ready"}` (`"failed"` = AI contradictions unavailable, rule-based ones
    are still shown)
- Interactive API: http://localhost:8000/docs

## 3. Automated checks

**Unit tests** (no API key, no real data needed, no cost):

```bash
.venv/bin/python -m pytest -q tests
```

All tests must pass. `tests/test_ai_trim.py` checks that the AI never shows a wrong dose, side or date.

**Pipeline evaluation on the 5 situations** (uses the API, ~1-2 cents, ~1 min):

```bash
.venv/bin/python scripts/bench_models.py --app
```

Per situation it prints latency, answers found, facts, `not_checked` (analysis incomplete) and rejected facts,
then a total line. Notes:

- The answer score is **our own keyword rubric** (from `cases/expected.json`), not the jury's method. It can be
  wrong in both directions. **Only the manual review below counts.**
- Contradiction scoring needs `bench_out/gold_contradictions.json`, which is git-ignored (derived from the data);
  without it, contradictions are listed but not scored. Ask Zarina for it if you need the score.
- Run it at least twice: results vary between runs.

## 4. Manual review (this is what counts)

For each of the 5 situations in `cases/situations.md`: send the **title + description** (not the questions) and
check the screen against `cases/expected.json`.

Send a situation without the UI:

```bash
curl -s localhost:8000/api/summary -H 'Content-Type: application/json' \
  -d '{"situation": "Pre-operative anaesthesia check. You are the anaesthetist. She is scheduled for a quadrantectomy (partial removal of the breast) with intraoperative radiotherapy on 16 May 2016."}' \
  | python3 -m json.tool
```

Check per situation:

1. **Correct answers:** each of the 3 questions is answered on the screen (no clicking needed), with a link to
   one of its expected source documents. A "not documented" answer needs no link. Wording may differ.
2. **Traceability:** open every fact's source (`GET /api/patients/0000-0001/documents/{doc_id}`, highlight
   `text[start:end]`). The passage must really support the fact, not just contain the words.
3. **Invented facts:** any fact on screen that is not in the documents? (Translations, generic drug names and
   calculated values such as a weight change are allowed.)
4. **Contradictions:** each one must be real (not a small measurement difference, not a value that changed over
   time) and link all its passages. Count real ones minus false alarms; each counts once across all screens.
5. **"Not documented" vs "analysis incomplete":** `not_documented` = searched all documents, not there.
   `not_checked` = the AI ran out of time or failed for that need (**not** proof of absence). Note every
   `not_checked` entry; if there are many, re-run the situation.
6. **Load time:** `timings_ms.total` in the response, or a stopwatch in the UI from submit to complete screen.
   Target: slowest of the 5 under 10 s.

Also try 2-3 **unseen situations** (the jury will do this), e.g.:

- "You are the physiotherapist planning mobilisation and arm exercises after her breast surgery."
- "You are the hospital pharmacist doing a medication reconciliation at admission for surgery."
- "You are the radiotherapist preparing the intraoperative radiotherapy."

Check that the screen changes with the role, shows relevant facts first, and makes no unsupported claims.

## 5. Record your results

Copy this table into the team doc (one per tester / run):

| Situation | Answers (of 3) | Wrong links | Invented facts | Contradictions shown (real / false) | not_checked | Load time |
|---|---|---|---|---|---|---|
| 1 Anaesthesia | | | | | | |
| 2 Surgery planning | | | | | | |
| 3 Diabetes review | | | | | | |
| 4 Emergency | | | | | | |
| 5 Open follow-ups | | | | | | |
| **Total** | **/15** | target 0 | target 0 | target net 5+ | | target < 10 s |

Note anything odd (German text in values, cluttered lists, missing answers) with the situation and fact.

## 6. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `"mock": true` in responses | no `NEBIUS_API_KEY` in `.env` | add the key, restart the server |
| Many `not_checked` entries, few facts, ~9 s every time | Nebius slow or connection timeouts | check the server log for `ConnectTimeout`; re-run later. Not a code bug |
| `ai_contradictions: "failed"` | Nebius failed during start-up | rule-based contradictions still show; restart the server to retry |
| `address already in use` | old server still on port 8000 | stop it, start again |
| `No patient data under ...` | `DATA_DIR` wrong | point it at the track repo's `data` folder |
