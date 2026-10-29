"""Sanity-check segmentation on the real documents.

    python scripts/inspect_data.py            # summary + suspicious passages
    python scripts/inspect_data.py --dump     # writes passages_dump.txt to eyeball every split
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import DATA_DIR  # noqa: E402
from backend.corpus import Corpus  # noqa: E402
from backend.verify import verify_quote  # noqa: E402

c = Corpus(DATA_DIR)
if not c.patients:
    sys.exit(f"No patient-* folders under {DATA_DIR}. Set DATA_DIR=/path/to/data")

for pid, p in c.patients.items():
    print(f"\nPatient {pid} — {p.name}: {len(p.documents)} documents, {len(p.passage_index)} passages, "
          f"IBM JSON files: {list(p.extractions)}")
    print(f"{'doc_id':40} {'date':10} {'pass':>5} {'chars':>7}  title")
    for d in p.documents.values():
        kinds = {}
        for ps in d.passages:
            kinds[ps.kind] = kinds.get(ps.kind, 0) + 1
        print(f"{d.doc_id:40} {d.date or '-':10} {len(d.passages):5} {len(d.text):7}  {d.title[:50]}   {kinds}")

    long_ = [ps for ps in p.passage_index.values() if len(ps.text) > 400]
    short = [ps for ps in p.passage_index.values() if ps.kind == "sentence" and len(ps.text) < 15]
    print(f"\nSuspicious: {len(long_)} passages > 400 chars (missed split?), {len(short)} sentences < 15 chars (bad split?)")
    for ps in (long_ + short)[:15]:
        print(f"  [{ps.id}] {' '.join(ps.text.split())[:160]!r}")

    # round-trip: every passage must verify as an exact quote in its own document
    bad = [ps.id for ps in p.passage_index.values()
           if len(ps.text) >= 8 and not verify_quote(p, ps.doc_id, ps.text).ok]
    print(f"Round-trip quote check: {len(p.passage_index) - len(bad)}/{len(p.passage_index)} OK {bad[:5]}")

    if "--dump" in sys.argv:
        out = Path("passages_dump.txt")
        with out.open("w", encoding="utf-8") as f:
            for ps in p.passage_index.values():
                f.write(f"[{ps.id}] ({ps.kind}) {' '.join(ps.text.split())}\n")
        print(f"Wrote {out.resolve()}")
