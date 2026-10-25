"""Customer and order access on an owned SQLite connection."""

import sqlite3
from types import TracebackType
from typing import Self

from .models import Customer, CustomerNotFoundError


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_customer(self, customer_id: int, display_name: str) -> Customer:
        with self.connection:
            self.connection.execute(
                'INSERT INTO customers (id, full_name) VALUES (?, ?)',
                (customer_id, display_name),
            )

        return Customer(customer_id, display_name)

    def get_customer(self, customer_id: int) -> Customer:
        row = self.connection.execute(
            'SELECT id, full_name FROM customers WHERE id = ?', (customer_id,)
        ).fetchone()

        if row is None:
            raise CustomerNotFoundError(customer_id)

        return Customer(row[0], row[1])

    def customers(self) -> list[Customer]:
        rows = self.connection.execute(
            'SELECT id, full_name FROM customers ORDER BY id'
        )
        return [Customer(row[0], row[1]) for row in rows]

    def create_order(self, order_id: int, customer_id: int, amount_cents: int) -> None:
        with self.connection:
            self.connection.execute(
                'INSERT INTO orders VALUES (?, ?, ?)',
                (order_id, customer_id, amount_cents),
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
