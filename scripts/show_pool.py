"""Build the verified fact pool from IBM's extraction and show what was corrected/dropped.

    python scripts/show_pool.py            # summary + every corrected or dropped entry
    python scripts/show_pool.py --all      # also list every fact
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import DATA_DIR  # noqa: E402
from backend.corpus import Corpus  # noqa: E402
from backend.ibm_pool import build_pool  # noqa: E402

for pid, pat in Corpus(DATA_DIR).patients.items():
    pool = build_pool(pat)
    print(f"Patient {pid}: {pool.stats}")
    print("\nStatus corrected from the document wording (IBM -> document):")
    for f in pool.facts:
        if f.status_corrected:
            print(f"  {f.id:5} {f.ibm_status:>10} -> {f.status:10} {' '.join(f.text.split())[:70]!r}  {f.passage_ids[0]}")
    print("\nNotes (allergy from documents, IBM cited wrong document):")
    for f in pool.facts:
        if f.note:
            print(f"  {f.id:5} {f.kind:10} {' '.join(f.text.split())[:60]!r}  {f.note}")
    print(f"\nDropped ({len(pool.dropped)}): IBM entries not found in the cited documents")
    for d in pool.dropped:
        print(f"  {d['file']:12} {d['text'][:70]!r}  docs={d['documents']}")
    if "--all" in sys.argv:
        print("\nAll facts:")
        for f in pool.facts:
            print(f"  {f.id:5} {f.kind:11} {f.status:10} {' '.join(f.text.split())[:70]!r} {f.value or ''} {f.doc_id}")
