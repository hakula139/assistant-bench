"""Place an order in the local store."""

from pathlib import Path

from .models import ConflictError, Order
from .store import Store


def place_order(db: Path, order_id: str, amount_cents: int) -> Order:
    if not order_id:
        raise ValueError('Order ID must be nonempty.')
    if type(amount_cents) is not int or not 0 < amount_cents <= 2**63 - 1:
        raise ValueError('Amount must be a positive signed 64-bit integer.')

    with Store(db) as store:
        existing = store.get_order(order_id)

        if existing is not None:
            if existing.amount_cents != amount_cents:
                raise ConflictError(f'Order {order_id} has different cents.')
            return existing

        order = Order(order_id, amount_cents)
        store.add_order(order)

    return order
