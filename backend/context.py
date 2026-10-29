"""Render the patient's documents for the model prompt, with passage IDs.

Format:
    === DOC <doc_id> | date <yyyy-mm-dd> | <title> ===
    [<doc_id>#p0] first passage
    [<doc_id>#p1] ...
Ask the model to cite {"doc_id", "passage_id", "quote"} with the quote copied verbatim.
"""
from __future__ import annotations

from .corpus import Patient


def llm_context(patient: Patient, doc_ids: list[str] | None = None) -> str:
    parts = [f"PATIENT {patient.patient_id} ({patient.name}) — {len(patient.documents)} documents"]
    for d in patient.documents.values():
        if doc_ids and d.doc_id not in doc_ids:
            continue
        parts.append(f"\n=== DOC {d.doc_id} | date {d.date or 'unknown'} | {d.title} ===")
        parts += [f"[{p.id}] {' '.join(p.text.split())}" for p in d.passages]
    return "\n".join(parts)
