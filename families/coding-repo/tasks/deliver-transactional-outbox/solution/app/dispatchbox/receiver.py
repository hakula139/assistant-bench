"""A durable fake receiver with the external deduplication contract."""

import sqlite3
from contextlib import closing
from pathlib import Path

from .models import DeliveryError


class FakeReceiver:
    def __init__(self, path: Path, *, fail_keys: frozenset[str] = frozenset()) -> None:
        self.path = path
        self.fail_keys = fail_keys

        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                'CREATE TABLE IF NOT EXISTS receipts '
                '(key TEXT PRIMARY KEY, payload BLOB NOT NULL)'
            )

    def send(self, key: str, payload: bytes) -> None:
        if key in self.fail_keys:
            raise DeliveryError(f'Delivery of {key} failed.')

        with closing(sqlite3.connect(self.path)) as connection, connection:
            row = connection.execute(
                'SELECT payload FROM receipts WHERE key = ?', (key,)
            ).fetchone()

            if row is not None:
                if row[0] != payload:
                    raise DeliveryError(f'Delivery of {key} changed its payload.')
                return

            connection.execute('INSERT INTO receipts VALUES (?, ?)', (key, payload))

    def effects(self) -> dict[str, bytes]:
        with closing(sqlite3.connect(self.path)) as connection:
            return dict(connection.execute('SELECT key, payload FROM receipts'))
