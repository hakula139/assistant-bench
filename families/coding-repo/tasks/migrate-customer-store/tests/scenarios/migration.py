import itertools
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from shopstore import (
    CustomerNotFoundError,
    MigrationError,
    SchemaVersionError,
    customer_json,
    customer_report,
    migrate,
    open_store,
)


CRASH_STATUS = 86
FAILURES: dict[str, type[Exception]] = {
    'MigrationError': MigrationError,
    'SchemaVersionError': SchemaVersionError,
}


def raised[E: BaseException](action: Callable[[], object], kind: type[E]) -> E | None:
    """Return what `action` raised of `kind`, or None when it returned."""
    try:
        action()
    except kind as error:
        return error
    return None


def snapshot(connection: sqlite3.Connection) -> list[Any]:
    return [
        connection.execute('PRAGMA user_version').fetchone()[0],
        connection.execute(
            'SELECT type, name, tbl_name, sql FROM sqlite_master '
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall(),
        connection.execute('SELECT * FROM customers ORDER BY id').fetchall(),
        connection.execute('SELECT * FROM orders ORDER BY id').fetchall(),
    ]


def schema(connection: sqlite3.Connection) -> dict[str, Any]:
    def indexed(index: str) -> list[str]:
        return [row[2] for row in connection.execute(f'PRAGMA index_info({index})')]

    connection.execute('SAVEPOINT null_name')
    null_name = raised(
        lambda: connection.execute(
            'INSERT INTO customers(id, display_name) VALUES (1000, NULL)'
        ),
        sqlite3.IntegrityError,
    )
    connection.execute('ROLLBACK TO null_name')
    connection.execute('RELEASE null_name')

    return {
        'null_name_rejected': null_name is not None,
        'columns': {
            row[1]: row for row in connection.execute('PRAGMA table_info(customers)')
        },
        'customers_name': indexed('customers_name'),
        'orders_customer_id': indexed('orders_customer_id'),
        'foreign_key_check': connection.execute('PRAGMA foreign_key_check').fetchall(),
    }


def legacy(path: str, legacy_schema: str, names: list[list[Any]]) -> None:
    """Write a version-1 store holding `names` and three fixed orders."""
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(legacy_schema)
        connection.executemany(
            'INSERT INTO customers(id, full_name) VALUES (?, ?)', names
        )
        connection.executemany(
            'INSERT INTO orders(id, customer_id, amount_cents) VALUES (?, ?, ?)',
            [(11, 2, 125), (12, 2, 375), (13, 7, 90)],
        )


def set_user_version(path: str, version: int) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f'PRAGMA user_version = {version}')


def stored(path: str) -> dict[str, Any]:
    """Read the store's snapshot through a separate plain connection."""
    with closing(sqlite3.connect(path)) as connection:
        return {
            'snapshot': snapshot(connection),
            'foreign_key_check': connection.execute(
                'PRAGMA foreign_key_check'
            ).fetchall(),
        }


def empty_store(path: str) -> dict[str, Any]:
    with open_store(Path(path)) as store:
        version = store.connection.execute('PRAGMA user_version').fetchone()[0]
        customer = store.create_customer(2, '  Zoë 王  ')
        empty = store.create_customer(7, '')
        store.create_order(10, customer.id, 450)
        seen = {
            'version': version,
            'customer': customer.display_name,
            'empty': empty.display_name,
            'json': customer_json(store, 2),
            'report': customer_report(store),
            'schema': schema(store.connection),
        }
        missing = raised(lambda: store.get_customer(999), CustomerNotFoundError)
    return {
        **seen,
        'missing': None if missing is None else {'customer_id': missing.customer_id},
    }


def open_legacy(path: str) -> dict[str, Any]:
    with open_store(Path(path)) as store:
        return {
            'version': store.connection.execute('PRAGMA user_version').fetchone()[0],
            'ids': [customer.id for customer in store.customers()],
            'report': customer_report(store),
            'json': customer_json(store, 2),
            'schema': schema(store.connection),
        }


def reopen_and_write(path: str) -> dict[str, Any]:
    with open_store(Path(path)) as reopened:
        report = customer_report(reopened)
        orphan = raised(
            lambda: reopened.connection.execute(
                'INSERT INTO orders VALUES (99, 999, 1)'
            ),
            sqlite3.IntegrityError,
        )
        reopened.connection.rollback()
        newcomer = reopened.create_customer(12, 'New')
        reopened.create_order(14, newcomer.id, 10)
        return {
            'report': report,
            'orphan_rejected': orphan is not None,
            'newcomer': reopened.get_customer(12).display_name,
            'total_cents': customer_report(reopened)['total_cents'],
            'schema': schema(reopened.connection),
        }


def repair_after_rollback(path: str) -> dict[str, Any]:
    """Fail to migrate a NULL name, then repair it and migrate on one connection."""
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        before = snapshot(connection)
        if raised(lambda: migrate(connection), MigrationError) is None:
            return {'rejected': False}
        rolled_back = {
            'rejected': True,
            'before': before,
            'after': snapshot(connection),
            'in_transaction': connection.in_transaction,
        }
        connection.execute("UPDATE customers SET full_name = 'repaired' WHERE id = 7")
        connection.commit()

        migrate(connection)

        return {
            **rolled_back,
            'version': connection.execute('PRAGMA user_version').fetchone()[0],
            'rows': connection.execute(
                'SELECT id, display_name FROM customers ORDER BY id'
            ).fetchall(),
            'schema': schema(connection),
        }


def crash_migration(path: str, statement: int) -> None:
    """Migrate the store, crashing as its SQL statement number `statement` starts.

    Exiting with `CRASH_STATUS` skips SQLite's cleanup, as a crash would.
    """
    started = itertools.count(1)

    def trace(_: str) -> None:
        if next(started) == statement:
            os._exit(CRASH_STATUS)

    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.set_trace_callback(trace)
        migrate(connection)


def open_store_rejection(path: str, error: str) -> dict[str, Any] | None:
    """Return the failure `open_store` raised of the `error` class, or None."""
    caught = raised(lambda: open_store(Path(path)).close(), FAILURES[error])
    if isinstance(caught, SchemaVersionError):
        return {'version': caught.version}
    return None if caught is None else {}


def migrate_unknown_version(path: str) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        error = raised(lambda: migrate(connection), SchemaVersionError)
        return {
            'error': None if error is None else {'version': error.version},
            'snapshot': snapshot(connection),
            'in_transaction': connection.in_transaction,
        }


def cli_report(path: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, '-m', 'shopstore', 'report', '--db', path],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return {
        'returncode': result.returncode,
        'stdout': result.stdout,
        'stderr': result.stderr,
    }


def report(path: str) -> dict[str, Any]:
    with open_store(Path(path)) as store:
        return {'report': customer_report(store), 'schema': schema(store.connection)}
