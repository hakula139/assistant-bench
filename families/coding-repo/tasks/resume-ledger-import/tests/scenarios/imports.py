import dataclasses
import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ledgerload import (
    ConflictError,
    LedgerRow,
    SourceError,
    Store,
    import_file,
)


ERRORS: dict[str, type[Exception]] = {
    'ConflictError': ConflictError,
    'SourceError': SourceError,
    'ValueError': ValueError,
    'sqlite3.Error': sqlite3.Error,
}
"""The errors a test may expect, by the name it expects them under."""


def import_source(
    db: str, source: str, source_id: str, **options: Any
) -> dict[str, Any]:
    """Import `source` into `db`, reporting the result or the error raised."""
    try:
        result = import_file(Path(db), Path(source), source_id=source_id, **options)
    except Exception as error:
        return {
            'raised': [
                name for name, kind in ERRORS.items() if isinstance(error, kind)
            ],
            'record_index': error.record_index
            if isinstance(error, SourceError)
            else None,
            'error': repr(error),
        }
    return {'result': dataclasses.asdict(result)}


def query(action: Callable[[], Any]) -> Any:
    """Return what `action` returns, or the error it raised.

    An error stays within its own query, so it fails only the assertions on it.
    """
    try:
        value = action()
        json.dumps(value)
    except Exception as error:
        return {'error': repr(error)}
    return value


def row(entry: LedgerRow) -> list[Any]:
    return [entry.external_id, entry.amount_cents, entry.memo]


def ledger(db: str, source_id: str | None = None) -> dict[str, Any]:
    """Report the rows and totals in `db`, with `source_id`'s progress if given."""
    with Store(Path(db)) as store:
        seen = {
            'rows': query(lambda: [row(entry) for entry in store.rows()]),
            'report': query(store.report),
        }
        if source_id is not None:
            seen['checkpoint'] = query(lambda: store.checkpoint(source_id))
            seen['digest'] = query(lambda: store.source_digest(source_id))
    return seen


def execute(db: str, statement: str) -> None:
    with Store(Path(db)) as store:
        store.connection.execute(statement)
        store.connection.commit()


def cli(arguments: list[str], check: bool = False) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, '-m', 'ledgerload', *arguments],
        capture_output=True,
        text=True,
        timeout=10,
        check=check,
    )
    return {
        'returncode': completed.returncode,
        'stdout': completed.stdout,
        'stderr': completed.stderr,
    }
