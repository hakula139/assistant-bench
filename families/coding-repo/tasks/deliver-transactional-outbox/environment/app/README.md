# Order delivery

Run `python -m dispatchbox place --db orders.sqlite --order-id ORDER --amount-cents 1200`, then `python -m dispatchbox dispatch --db orders.sqlite --receiver receiver.sqlite`. Each command prints one JSON value and exits 0. Invalid inputs and failures produce stderr and a nonzero exit.

## Required public interface

The package exports `Order(order_id: str, amount_cents: int)`, `Notification(id: int, order_id: str, key: str, payload: bytes)`, `DispatchResult(attempted: int, delivered: int, failed: int)`, `ConflictError`, `DeliveryError`, `Transport`, `FakeReceiver`, `Store`, `place_order` and `dispatch_pending`.

`place_order(db: Path, order_id: str, amount_cents: int) -> Order` requires a nonempty ID and positive signed 64-bit integer cents, excluding bool. It atomically persists an order and one pending notification. Repeating identical input returns the order without adding work. Changed cents raise ConflictError.

`dispatch_pending(db: Path, transport: Transport, *, limit: int = 100) -> DispatchResult` requires a positive integer limit, excluding bool, and counts attempted pending records in insertion order. DeliveryError leaves a record pending and permits later records to proceed. Each success is marked delivered and committed independently. Other failures propagate. Already delivered records are not attempted.

`Transport.send(key: str, payload: bytes) -> None` either accepts or raises DeliveryError. A receiver deduplicates accepted effects by key. Every notification uses `order:<order_id>` and the same stored JSON bytes on every attempt. Payload JSON contains order_id and amount_cents. JSON ordering is immaterial.

`Store(db: Path)` exposes an open SQLite `connection`, `get_order(order_id) -> Order | None`, `orders() -> list[Order]`, `pending(limit) -> list[Notification]`, `mark_delivered(notification_id) -> None`, and `close()`. It is a context manager. Lists follow insertion order. mark_delivered updates the flag and commits.

`FakeReceiver(path: Path, *, fail_keys: frozenset[str] = frozenset())` implements Transport using a separate SQLite database. `effects() -> dict[str, bytes]` returns accepted effects. Configured keys raise DeliveryError. Repeated identical bytes for a key have one effect, while changed bytes raise DeliveryError.

## Persisted format

The completed schema retains orders with order_id and amount_cents, and adds outbox with an increasing id, a unique order_id foreign key, a unique idempotency_key, payload as a BLOB, and delivered as 0 or 1. Tests may install ordinary SQLite triggers on outbox insertion or updates of outbox.delivered. The receiver's separate receipts table stores key and payload.

## Starter behavior

The starter schema stores orders alone. Store.add_order commits an order, and Store.pending derives notifications from current order values. Store.mark_delivered remembers successful deliveries only until that Store closes. Dispatch sends immediately and stops on a delivery error. The supplied transport protocol and durable deduplicating receiver are usable as provided. Add durable notification storage and integrate it with placement and dispatch to satisfy the required contracts. Starter databases need no migration support.
