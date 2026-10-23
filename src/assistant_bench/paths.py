"""Repo and data directory locations, and atomic file writes."""

import os
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FAMILIES_DIR = REPO_ROOT / 'families'
REPORTS_DIR = REPO_ROOT / 'reports'
DATA_DIR_VAR = 'BENCH_DATA_DIR'


def data_dir() -> Path:
    """Return the home of data kept out of Git.

    `BENCH_DATA_DIR` names it, and `$XDG_DATA_HOME/assistant-bench` is the default.
    """
    if path := os.environ.get(DATA_DIR_VAR):
        return Path(path)
    base = os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share'
    return Path(base) / 'assistant-bench'


def write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text`, creating its directory if needed.

    A reader sees the old file or the new one whole, and an interrupted write
    leaves the old file in place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', dir=path.parent, prefix=f'.{path.name}.', delete_on_close=False
    ) as file:
        file.write(text)
        file.close()
        os.replace(file.name, path)
