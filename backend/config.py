"""Paths and constants. Data stays OUTSIDE git (see .gitignore) — set DATA_DIR if it lives elsewhere."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Read KEY=value lines from the git-ignored .env; real environment variables take precedence."""
    if path.exists():
        for line in path.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#") and v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_dotenv()   # before DATA_DIR, so DATA_DIR can be set in .env
DATA_DIR = Path(os.environ.get("DATA_DIR", REPO_ROOT / "data")).resolve()

# Patient display names (synthetic). Folder name "patient-<id>" -> id.
PATIENT_NAMES = {"0000-0001": "Anna Musterfrau"}

SUPPORTED_DOC_EXT = {".json", ".txt", ".md", ".html", ".htm", ".xml", ".pdf"}
