"""Locations of committed inputs and of the local data directory."""

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FAMILIES_DIR = REPO_ROOT / 'families'
REPORTS_DIR = REPO_ROOT / 'reports'
DATA_DIR_VAR = 'BENCH_DATA_DIR'


def data_dir() -> Path:
    """Return the home of data kept out of Git.

    `BENCH_DATA_DIR` names it when set, and it defaults to
    `$XDG_DATA_HOME/assistant-bench`.
    """
    if path := os.environ.get(DATA_DIR_VAR):
        return Path(path)
    base = os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share'
    return Path(base) / 'assistant-bench'
