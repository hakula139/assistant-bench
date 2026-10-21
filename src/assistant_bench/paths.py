"""Locations of committed inputs and of the local data directory."""

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FAMILIES_DIR = REPO_ROOT / 'families'
REPORTS_DIR = REPO_ROOT / 'reports'


def data_dir() -> Path:
    """Return `$XDG_DATA_HOME/assistant-bench`, the home of data kept out of Git."""
    base = os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share'
    return Path(base) / 'assistant-bench'
