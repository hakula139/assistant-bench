"""Ledger rows and durable source checkpoint storage."""

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from .models import LedgerRow


class Store:
    """Own a connection. Public tables are defined in schema.sql."""

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

    def rows(self) -> list[LedgerRow]:
        return [
            LedgerRow(str(row[0]), int(row[1]), str(row[2]))
            for row in self.connection.execute(
                'SELECT external_id, amount_cents, memo FROM ledger '
                'ORDER BY external_id'
            )
        ]

    def checkpoint(self, source_id: str) -> int:
        row = self.connection.execute(
            'SELECT checkpoint FROM sources WHERE source_id = ?', (source_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def source_digest(self, source_id: str) -> str | None:
        row = self.connection.execute(
            'SELECT sha256 FROM sources WHERE source_id = ?', (source_id,)
        ).fetchone()
        return str(row[0]) if row else None

    def report(self) -> dict[str, int]:
        count = total = 0
        for row in self.connection.execute('SELECT amount_cents FROM ledger'):
            count += 1
            total += int(row[0])

        return {'rows': count, 'total_cents': total}
