# Order delivery

Run `python -m dispatchbox place --db orders.sqlite --order-id ORDER --amount-cents 1200`, then `python -m dispatchbox dispatch --db orders.sqlite --receiver receiver.sqlite`. Each command prints one JSON value and exits 0. Invalid inputs and failures produce stderr and a nonzero exit.

## Public interface

The package exports `Order(order_id: str, amount_cents: int)`, `Notification(id: int, order_id: str, key: str, payload: bytes)`, `DispatchResult(attempted: int, delivered: int, failed: int)`, `ConflictError`, `DeliveryError`, `Transport`, `FakeReceiver`, `Store`, `place_order` and `dispatch_pending`.

`place_order(db: Path, order_id: str, amount_cents: int) -> Order` requires a nonempty ID and cents that are a positive signed 64-bit integer, excluding bool. It commits the order and its one pending notification in a single transaction, so a failure persists neither. Repeating the same ID and cents returns the existing order without adding an order or a notification. Repeating the ID with different cents raises ConflictError.

`dispatch_pending(db: Path, transport: Transport, *, limit: int = 100) -> DispatchResult` requires a positive integer limit, excluding bool. It attempts at most `limit` pending notifications in insertion order and reports how many it attempted, delivered, and failed. A DeliveryError leaves that notification pending and dispatch continues with the next one. Each accepted notification is marked delivered in its own commit, so earlier deliveries stay marked when a later one fails. Any other exception, including a database error, propagates. Delivered notifications are never attempted again.

`Transport.send(key: str, payload: bytes) -> None` either accepts or raises DeliveryError. A receiver deduplicates accepted effects by key. Each notification's key is `order:<order_id>`, and every attempt sends the payload bytes stored when the order was placed, never bytes rebuilt from the order. The payload is a JSON object containing order_id and amount_cents, in any key order.

`Store(db: Path)` creates the schema if needed and exposes an open SQLite `connection`, `get_order(order_id) -> Order | None`, `orders() -> list[Order]`, `pending(limit) -> list[Notification]`, `mark_delivered(notification_id) -> None`, and `close()`. It is a context manager. Lists follow insertion order. `pending` returns the stored notifications not yet delivered, and `mark_delivered` sets the delivered flag and commits.

`FakeReceiver(path: Path, *, fail_keys: frozenset[str] = frozenset())` implements Transport using a separate SQLite database. `effects() -> dict[str, bytes]` returns accepted effects. Configured keys raise DeliveryError. Repeated identical bytes for a key have one effect, while changed bytes raise DeliveryError.

## Persisted format

The orders table holds order_id and amount_cents. The outbox table holds an increasing id, a unique order_id referencing orders, a unique idempotency_key, the payload as a BLOB, and delivered as 0 or 1. Tests may install ordinary SQLite triggers on outbox insertion or on updates of outbox.delivered. The receiver's separate receipts table stores key and payload.
