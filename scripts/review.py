"""Manual review helper: for each situation, show the questions + expected answers (cases/) next to the screen the
running server returns, so a human can judge correctness, traceability and contradictions.

    .venv/bin/python scripts/review.py            # all 5 situations (server must be running on :8000)
    .venv/bin/python scripts/review.py 1 4        # only situations 1 and 4
    .venv/bin/python scripts/review.py > review.txt   # save (contains patient text: do not commit)
    .venv/bin/python scripts/review.py --text "You are the cardiologist ..."   # a NEW situation (no expected answers)
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://localhost:8000"


def situations() -> dict:
    out = {}
    for block in re.split(r"^## ", (ROOT / "cases" / "situations.md").read_text(), flags=re.M)[1:]:
        head, *rest = block.strip().splitlines()
        num, title = head.split(".", 1)
        desc = " ".join(l.strip() for l in rest if l.strip() and not re.match(r"\d+\.", l.strip()))
        out[num.strip()] = f"{title.strip()}. {desc}"
    return out


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)     # write to review.txt as it goes, not only at the end
    sits = situations()
    expected: dict[str, list] = {}
    for q in json.loads((ROOT / "cases" / "expected.json").read_text()):
        expected.setdefault(q["id"].split(".")[0], []).append(q)
    args = sys.argv[1:]
    if args[:1] == ["--text"]:                     # new situation: no expected answers, judge against documents
        sits, args = {"new": " ".join(args[1:])}, ["new"]
    for sid in args or sorted(sits):
        t = time.perf_counter()
        r = post("/api/summary", {"situation": sits[sid]})
        dt = time.perf_counter() - t
        print(f"\n{'=' * 100}\nSITUATION {sid}: {sits[sid]}\nload time {dt:.1f}s (server total "
              f"{r['timings_ms'].get('total', 0) / 1000:.1f}s){'   *** MOCK, no AI ***' if r.get('mock') else ''}")
        print(f"information needs the app planned (priority order): {r['categories']}")
        if sid in expected:
            print("\n--- QUESTIONS (expected answer | expected source docs) -> mark each: correct / partly / wrong / missing")
            for q in expected[sid]:
                print(f"  {q['id']} {q['question']}\n       expected: {q['answer']}  | docs {q['sources'] or 'none (not documented)'}")
        else:
            print("\n--- NEW SITUATION: no expected answers. Write down first what YOU would need for this task, then check below.")
        print("\n--- SCREEN: FACTS (check: does the quote really support the value?)")
        for f in r["facts"]:
            print(f"  [{f['category']}] {f['label']}: {f['value']}")
            for s in f["sources"]:
                print(f"       <- {s['doc_id']} {s['doc_date']} {s['passage_ids']}: \"{s['quote'][:110]}\"")
        print("\n--- NOT DOCUMENTED (searched, not found):", *(f"\n  - {m['item']}" for m in r["not_documented"]) or [" -"])
        print("--- ANALYSIS INCOMPLETE (not checked, NOT proof of absence):", *(f"\n  - {x}" for x in r.get("not_checked", [])) or [" -"])
        print("\n--- CONTRADICTIONS (check: real? not a measurement difference / change over time? all passages linked?)")
        for c in r["contradictions"]:
            print(f"  * {c['summary']}  [{c.get('origin')}]")
            for cl in c["claims"]:
                print(f"       <- {cl['source']['doc_id']} {cl['source']['passage_ids']}: \"{cl['source']['quote'][:90]}\"")


if __name__ == "__main__":
    main()
