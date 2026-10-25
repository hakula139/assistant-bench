"""Construct the version-2 tables and their public indexes."""

import sqlite3


def create_tables(connection: sqlite3.Connection, *, temporary: bool) -> None:
    customers = 'customers_new' if temporary else 'customers'
    orders = 'orders_new' if temporary else 'orders'
    connection.execute(
        f'CREATE TABLE {customers} (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL)'
    )
    connection.execute(
        f'CREATE TABLE {orders} '
        '(id INTEGER PRIMARY KEY, '
        f'customer_id INTEGER NOT NULL REFERENCES {customers}(id), '
        'amount_cents INTEGER NOT NULL)'
    )


def create_indexes(connection: sqlite3.Connection) -> None:
    connection.execute('CREATE INDEX customers_name ON customers(display_name)')
    connection.execute('CREATE INDEX orders_customer_id ON orders(customer_id)')
