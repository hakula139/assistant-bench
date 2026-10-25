"""SQLite storage for placed orders."""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import TracebackType
from typing import Self

from .models import Notification, Order


class Store:
    def __init__(self, db: Path) -> None:
        self.connection = sqlite3.connect(db)
        self.delivered: set[int] = set()

        try:
            self.connection.executescript(
                Path(__file__).with_name('schema.sql').read_text()
            )
        except BaseException:
            self.connection.close()
            raise

    def get_order(self, order_id: str) -> Order | None:
        row = self.connection.execute(
            'SELECT order_id, amount_cents FROM orders WHERE order_id = ?', (order_id,)
        ).fetchone()
        return None if row is None else Order(row[0], row[1])

    def orders(self) -> list[Order]:
        rows = self.connection.execute(
            'SELECT order_id, amount_cents FROM orders ORDER BY rowid'
        )
        return [Order(row[0], row[1]) for row in rows]

    def add_order(self, order: Order) -> None:
        with self.connection:
            self.connection.execute(
                'INSERT INTO orders VALUES (?, ?)',
                (order.order_id, order.amount_cents),
            )

    def pending(self, limit: int) -> list[Notification]:
        rows = self.connection.execute(
            'SELECT rowid, order_id, amount_cents FROM orders ORDER BY rowid'
        )
        notifications: list[Notification] = []

        for identifier, order_id, amount_cents in rows:
            if identifier in self.delivered:
                continue

            order = Order(order_id, amount_cents)
            payload = json.dumps(
                asdict(order), ensure_ascii=False, separators=(',', ':'), sort_keys=True
            ).encode()
            notifications.append(
                Notification(identifier, order_id, f'order:{order_id}', payload)
            )

            if len(notifications) == limit:
                break

        return notifications

    def mark_delivered(self, notification_id: int) -> None:
        self.delivered.add(notification_id)

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
