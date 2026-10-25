"""Placement variant with separately durable order writes."""

import json
from pathlib import Path

from dispatchbox.models import ConflictError, Order
from dispatchbox.store import Store


def place_order(db: Path, order_id: str, amount_cents: int) -> Order:
    if not order_id:
        raise ValueError('Order ID must be nonempty.')
    if type(amount_cents) is not int or not 0 < amount_cents <= 2**63 - 1:
        raise ValueError('Amount must be a positive signed 64-bit integer.')

    with Store(db) as store:
        store.connection.execute('BEGIN IMMEDIATE')

        with store.connection:
            existing = store.get_order(order_id)

            if existing is not None:
                if existing.amount_cents != amount_cents:
                    raise ConflictError(f'Order {order_id} has different cents.')
                return existing

            payload = json.dumps(
                {'order_id': order_id, 'amount_cents': amount_cents},
                ensure_ascii=False,
                separators=(',', ':'),
                sort_keys=True,
            ).encode()
            store.connection.execute(
                'INSERT INTO orders VALUES (?, ?)', (order_id, amount_cents)
            )
            store.connection.commit()
            store.connection.execute(
                'INSERT INTO outbox (order_id, idempotency_key, payload) '
                'VALUES (?, ?, ?)',
                (order_id, f'order:{order_id}', payload),
            )

    return Order(order_id, amount_cents)
