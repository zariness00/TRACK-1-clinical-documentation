"""Paths and constants. Data stays OUTSIDE git (see .gitignore) — set DATA_DIR if it lives elsewhere."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", REPO_ROOT / "data")).resolve()

# Patient display names (synthetic). Folder name "patient-<id>" -> id.
PATIENT_NAMES = {"0000-0001": "Anna Musterfrau"}

SUPPORTED_DOC_EXT = {".json", ".txt", ".md", ".html", ".htm", ".xml", ".pdf"}
