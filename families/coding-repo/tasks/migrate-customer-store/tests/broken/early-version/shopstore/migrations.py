"""Migration variant committing its version before conversion."""

import sqlite3
from pathlib import Path

from shopstore.models import MigrationError, SchemaVersionError
from shopstore.repository import Store
from shopstore.schema import create_indexes, create_tables


def migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute('PRAGMA user_version').fetchone()[0]

    if version == 2:
        return
    if version not in {0, 1}:
        raise SchemaVersionError(version)

    connection.execute('PRAGMA user_version = 2')
    connection.commit()

    with connection:
        connection.execute('BEGIN IMMEDIATE')

        if version == 1:
            missing = connection.execute(
                'SELECT id FROM customers WHERE full_name IS NULL LIMIT 1'
            ).fetchone()

            if missing is not None:
                raise MigrationError(f'Customer {missing[0]} has a NULL name.')

            create_tables(connection, temporary=True)
            connection.execute(
                'INSERT INTO customers_new SELECT id, full_name FROM customers'
            )
            connection.execute(
                'INSERT INTO orders_new '
                'SELECT id, customer_id, amount_cents FROM orders'
            )
            connection.execute('DROP TABLE orders')
            connection.execute('DROP TABLE customers')
            connection.execute('ALTER TABLE customers_new RENAME TO customers')
            connection.execute('ALTER TABLE orders_new RENAME TO orders')
        else:
            create_tables(connection, temporary=False)

        create_indexes(connection)
        connection.execute('PRAGMA user_version = 2')


def open_store(path: Path) -> Store:
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA foreign_keys = ON')

    try:
        migrate(connection)
    except BaseException:
        connection.close()
        raise

    return Store(connection)
