"""Open stores using the baseline schema."""

import sqlite3
from pathlib import Path

from .models import SchemaVersionError
from .repository import Store
from .schema import V1_SQL


def migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute('PRAGMA user_version').fetchone()[0]

    if version == 0:
        connection.executescript(V1_SQL.read_text())
    elif version != 1:
        raise SchemaVersionError(version)


def open_store(path: Path) -> Store:
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA foreign_keys = ON')

    try:
        migrate(connection)
    except BaseException:
        connection.close()
        raise

    return Store(connection)
