"""Immutable events with monotonic ingestion sequences."""

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from .models import Event


class Store:
    """Append-only event storage. seq is an immutable ingestion sequence."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.executescript(
            Path(__file__).with_name('schema.sql').read_text()
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.connection.close()

    def append(self, event: Event) -> None:
        if not event.id:
            raise ValueError('Event ID must be nonempty.')

        if type(event.occurred_at) is not int:
            raise ValueError('Event time must be an integer.')

        with self.connection:
            self.connection.execute(
                'INSERT INTO events (id, occurred_at, tag) VALUES (?, ?, ?)',
                (event.id, event.occurred_at, event.tag),
            )

    def highwater(self) -> int:
        row = self.connection.execute(
            'SELECT coalesce(max(seq), 0) FROM events'
        ).fetchone()
        return int(row[0])
