"""Tiny keyword search over passages (no dependencies). Good enough for the mock
and as a passage index if sending all 8 documents per call gets too slow."""
from __future__ import annotations

import math
from collections import Counter

from .corpus import Patient
from .segment import Passage
from .text_norm import tokens


class PassageIndex:
    def __init__(self, patient: Patient):
        self.passages: list[Passage] = list(patient.passage_index.values())
        self.toks = [tokens(p.text) for p in self.passages]
        df = Counter(t for ts in self.toks for t in set(ts))
        n = max(len(self.passages), 1)
        self.idf = {t: math.log(1 + n / c) for t, c in df.items()}

    def search(self, query: str, k: int = 10, doc_id: str | None = None) -> list[tuple[float, Passage]]:
        qt = tokens(query)
        out = []
        for p, ts in zip(self.passages, self.toks):
            if doc_id and p.doc_id != doc_id:
                continue
            score = 0.0
            for q in qt:
                # prefix match handles German compounds/inflection: "allergie" ~ "allergien"
                if any(t.startswith(q) or (len(q) >= 5 and q in t) for t in ts):
                    score += self.idf.get(q, 1.0)
            if score:
                out.append((score / (1 + 0.002 * len(p.text)), p))
        out.sort(key=lambda x: -x[0])
        return out[:k]
