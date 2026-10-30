# CLAUDE.md — Track 1 clinical documentation (AIM Hackathon)

Read `docs/INTEGRATION_BRIEF.md` before changing anything. It holds the goal, the contracts between the parts,
the UI spec and the metrics we are judged on.

## Hard rules
- **Never commit patient data.** `data/` is git-ignored. Do not copy document text, IBM JSON or anything derived
  from them (quotes, reports) into tracked files. `ibm_vs_documents.md` is git-ignored for that reason.
- **The AI quotes, the code verifies.** Nothing reaches the screen unless `backend/validate.py` verified it
  (quote exists, numbers, laterality, polarity, negation, certainty). Never bypass or weaken these checks to make a
  test pass; fix the prompt or the data flow instead.
- **Every displayed answer needs a link** to its exact passage (`doc_id` + `start`/`end` in the cleaned text from
  `GET /api/patients/{pid}/documents/{doc_id}`). "Not documented" answers need no link.
- Python 3.9–3.13 compatible: use `Optional[...]`, not `X | None`, in pydantic models and FastAPI signatures.
- Run `python -m pytest -q tests` before every commit. Keep all tests green.

## Run
```bash
pip install -r requirements.txt
export DATA_DIR=~/TRACK-1-clinical-documentation/data   # the organisers' data, outside this repo
export NEBIUS_API_KEY=...                               # for backend/ai.py; never commit
uvicorn backend.api:app --reload --port 8000            # http://localhost:8000/docs
```
