"""Load the patient's documents once at start-up, segment them and keep them in memory.

doc_id = file name without extension (stable, human readable, and what the IBM
JSON files are likely to reference — check data/README.md and adjust
`_doc_id()` if they use another key).
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import DATA_DIR, PATIENT_NAMES, SUPPORTED_DOC_EXT
from .segment import Passage, segment
from .text_norm import normalize_with_map

_DATE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?((?:19|20)\d{2})\b")


@dataclass
class Document:
    doc_id: str
    patient_id: str
    filename: str
    title: str
    date: str | None          # ISO yyyy-mm-dd, first date found in the document (heuristic)
    text: str                  # raw text; ALL offsets refer to this
    passages: list[Passage] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)   # type, department, admission... (JSON docs)
    # normalised views for quote matching (built lazily)
    _norm: dict = field(default_factory=dict, repr=False)

    def norm(self, casefold: bool):
        if casefold not in self._norm:
            self._norm[casefold] = normalize_with_map(self.text, casefold)
        return self._norm[casefold]

    def meta(self) -> dict:
        m = self.metadata
        return {"doc_id": self.doc_id, "filename": self.filename, "title": self.title,
                "date": self.date, "type": m.get("type"), "department": m.get("department"),
                "admission": m.get("admission"),
                "n_passages": len(self.passages), "n_chars": len(self.text)}


@dataclass
class Patient:
    patient_id: str
    name: str
    documents: dict[str, Document]
    extractions: dict[str, object]       # IBM JSON files, by file stem (hints only — documents win)
    passage_index: dict[str, Passage] = field(default_factory=dict)

    def meta(self) -> dict:
        return {"patient_id": self.patient_id, "name": self.name,
                "documents": [d.meta() for d in self.documents.values()]}


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", "\t", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def _br_text(s: str) -> str:
    """IBM document text: '<BR>' line breaks + HTML entities ('&gt;')."""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).replace("\r\n", "\n")


def read_json_document(path: Path) -> tuple[str, dict]:
    """Track-1 format: {"metadata": {id, title, date, type, department, admission...}, "text": "...<BR>..."}"""
    obj = json.loads(path.read_text(encoding="utf-8"))
    return _br_text(obj.get("text", "")), obj.get("metadata", {}) or {}


def read_document_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".json":
        return read_json_document(path)[0]
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in {".html", ".htm", ".xml"}:
        raw = _html_to_text(raw)
    return raw.replace("\r\n", "\n")


def _doc_id(path: Path) -> str:
    return path.stem


def _first_date(text: str) -> str | None:
    """First date that is not a birth date (heuristic — verify against data/README.md metadata)."""
    for m in _DATE.finditer(text):
        before = text[max(0, m.start() - 20):m.start()].lower()
        if "geb" in before or "*" in before:
            continue
        d, mth, y = m.groups()
        return f"{y}-{int(mth):02d}-{int(d):02d}"
    return None


def _title(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""


def load_patient(patient_dir: Path) -> Patient:
    pid = patient_dir.name.removeprefix("patient-")
    docs: dict[str, Document] = {}
    doc_dir = patient_dir / "documents"
    for path in sorted(doc_dir.iterdir()) if doc_dir.exists() else []:
        if path.suffix.lower() not in SUPPORTED_DOC_EXT or path.name.startswith("."):
            continue
        meta: dict = {}
        if path.suffix.lower() == ".json":
            text, meta = read_json_document(path)
        else:
            text = read_document_text(path)
        did = str(meta.get("id") or _doc_id(path))
        doc = Document(did, pid, path.name, meta.get("title") or _title(text),
                       meta.get("date") or _first_date(text), text, metadata=meta)
        doc.passages = segment(did, text)
        docs[did] = doc
    extractions = {}
    for jp in sorted(patient_dir.glob("*.json")):
        try:
            extractions[jp.stem] = json.loads(jp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    p = Patient(pid, PATIENT_NAMES.get(pid, pid), docs, extractions)
    p.passage_index = {ps.id: ps for d in docs.values() for ps in d.passages}
    return p


class Corpus:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = Path(data_dir)
        self.patients: dict[str, Patient] = {}
        if self.data_dir.exists():
            for pdir in sorted(self.data_dir.glob("patient-*")):
                if pdir.is_dir():
                    pat = load_patient(pdir)
                    self.patients[pat.patient_id] = pat

    def patient(self, pid: str) -> Patient:
        if pid not in self.patients:
            raise KeyError(pid)
        return self.patients[pid]


@lru_cache(maxsize=4)
def get_corpus(data_dir: str = str(DATA_DIR)) -> Corpus:
    return Corpus(Path(data_dir))
