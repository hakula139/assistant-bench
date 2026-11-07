"""Connections to the local or hosted vote store."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal

from assistant_bench.arena import d1, local
from assistant_bench.database import Database


TargetName = Literal['staging', 'production', 'local']


@contextmanager
def connect(target: TargetName) -> Generator[Database]:
    """Open the selected store, holding the local store's lock when needed."""
    with local.connect() if target == 'local' else d1.connect(target) as database:
        yield database
