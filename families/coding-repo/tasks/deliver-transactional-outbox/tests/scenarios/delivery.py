import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dispatchbox import (
    ConflictError,
    DeliveryError,
    DispatchResult,
    FakeReceiver,
    Order,
    Store,
    dispatch_pending,
    place_order,
)


class Receiver:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.attempts: list[tuple[str, bytes]] = []
        self.effects: dict[str, bytes] = {}

    def send(self, key: str, payload: bytes) -> None:
        self.attempts.append((key, payload))
        if key in self.failures:
            raise DeliveryError('Temporarily unavailable.')
        if key in self.effects and self.effects[key] != payload:
            raise DeliveryError('Conflicting retry payload.')
        self.effects[key] = payload


def new_db() -> Path:
    return Path(tempfile.mkdtemp()) / 'orders.sqlite'


def order(value: Order | None) -> list[Any] | None:
    return None if value is None else [value.order_id, value.amount_cents]


def pending(store: Store) -> list[dict[str, Any]]:
    """Return every pending notification with its payload as hex."""
    return [
        {'order_id': item.order_id, 'key': item.key, 'payload': item.payload.hex()}
        for item in store.pending(100)
    ]


def payloads(effects: dict[str, bytes]) -> dict[str, str]:
    return {key: payload.hex() for key, payload in effects.items()}


def counts(result: DispatchResult) -> list[int]:
    return [result.attempted, result.delivered, result.failed]


def raises(
    error: type[BaseException], action: Callable[..., object], *args: Any, **kwargs: Any
) -> bool:
    """Tell whether calling `action` raised `error`, letting other errors through."""
    try:
        action(*args, **kwargs)
    except error:
        return True
    return False


def has_outbox(store: Store) -> bool:
    return (
        store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'outbox'"
        ).fetchone()
        is not None
    )


def identical_placement() -> dict[str, Any]:
    db = new_db()
    placed = place_order(db, 'café-1', 1250)
    repeated = place_order(db, 'café-1', 1250)
    conflicted = raises(ConflictError, place_order, db, 'café-1', 1251)
    with Store(db) as store:
        orders = [order(item) for item in store.orders()]
        notifications = pending(store)
    return {
        'order': order(placed),
        'repeated': order(repeated),
        'conflicted': conflicted,
        'orders': orders,
        'pending': notifications,
    }


def rollback_on_insert_failure() -> dict[str, Any]:
    db = new_db()
    with Store(db) as store:
        if not has_outbox(store):
            return {'outbox': False}
        store.connection.executescript(
            """
            CREATE TRIGGER reject_notification
            BEFORE INSERT ON outbox
            WHEN NEW.order_id = 'blocked'
            BEGIN
                SELECT RAISE(ABORT, 'Rejected notification');
            END;
            """
        )

    rejected = raises(sqlite3.DatabaseError, place_order, db, 'blocked', 500)
    with Store(db) as store:
        blocked = order(store.get_order('blocked'))
        rolled_back = pending(store)
        store.connection.execute('DROP TRIGGER reject_notification')
        store.connection.commit()
    recovered = place_order(db, 'blocked', 500)
    with Store(db) as store:
        after = pending(store)
    return {
        'outbox': True,
        'rejected': rejected,
        'blocked': blocked,
        'rolled_back': rolled_back,
        'recovered': order(recovered),
        'after': after,
    }


def delivery_limit() -> dict[str, Any]:
    db = new_db()
    for name, amount in [('a', 100), ('b', 200), ('c', 300)]:
        place_order(db, name, amount)
    receiver = Receiver({'order:a'})
    with Store(db) as store:
        stored = {item['key']: item['payload'] for item in pending(store)}

    first = counts(dispatch_pending(db, receiver, limit=2))
    first_attempts = [key for key, _ in receiver.attempts]
    with Store(db) as store:
        first_pending = pending(store)
    second = counts(dispatch_pending(db, receiver))
    second_attempts = [key for key, _ in receiver.attempts]
    receiver.failures.clear()
    third = counts(dispatch_pending(db, receiver))
    empty = counts(dispatch_pending(db, receiver))
    return {
        'stored': stored,
        'first': first,
        'first_attempts': first_attempts,
        'first_pending': first_pending,
        'second': second,
        'second_attempts': second_attempts,
        'third': third,
        'empty': empty,
        'effects': payloads(receiver.effects),
        'attempts': [[key, payload.hex()] for key, payload in receiver.attempts],
    }


def acceptance_before_database_failure() -> dict[str, Any]:
    db = new_db()
    for name, amount in [('a', 100), ('b', 200), ('c', 300)]:
        place_order(db, name, amount)
    receiver = FakeReceiver(db.parent / 'receiver.sqlite')
    with Store(db) as store:
        stored = {item['key']: item['payload'] for item in pending(store)}
        if not has_outbox(store):
            return {'outbox': False}
        store.connection.executescript(
            """
            CREATE TRIGGER reject_delivered
            BEFORE UPDATE OF delivered ON outbox
            WHEN NEW.order_id = 'b' AND NEW.delivered = 1
            BEGIN
                SELECT RAISE(ABORT, 'Delivered mark unavailable');
            END;
            """
        )

    failed = raises(sqlite3.DatabaseError, dispatch_pending, db, receiver)
    accepted = payloads(receiver.effects())
    with Store(db) as store:
        left = pending(store)
        store.connection.execute('DROP TRIGGER reject_delivered')
        store.connection.commit()
    retried = counts(dispatch_pending(db, receiver))
    with Store(db) as store:
        after = pending(store)
    return {
        'outbox': True,
        'stored': stored,
        'failed': failed,
        'accepted': accepted,
        'left': left,
        'retried': retried,
        'effects': payloads(receiver.effects()),
        'after': after,
    }


def invalid_inputs(amounts: list[int], limits: list[int]) -> dict[str, Any]:
    """Reject each amount and limit in turn against one database."""
    db = new_db()
    rejected_amounts = [
        raises(ValueError, place_order, db, 'bad', amount) for amount in amounts
    ]
    rejected_id = raises(ValueError, place_order, db, '', 10)
    good = place_order(db, 'good', 2**63 - 1)
    receiver = Receiver()
    rejected_limits = [
        raises(ValueError, dispatch_pending, db, receiver, limit=limit)
        for limit in limits
    ]
    with Store(db) as store:
        orders = [order(item) for item in store.orders()]
        notifications = pending(store)
    return {
        'amounts': rejected_amounts,
        'order_id': rejected_id,
        'good': order(good),
        'limits': rejected_limits,
        'attempts': len(receiver.attempts),
        'orders': orders,
        'pending': notifications,
    }


def cli_pipeline(commands: list[list[str]]) -> dict[str, Any]:
    """Run each command with `{db}` and `{receiver}` filled in."""
    directory = Path(tempfile.mkdtemp())
    db = directory / 'orders.sqlite'
    receiver = directory / 'cli-receiver.sqlite'
    runs: list[dict[str, Any]] = []
    for args in commands:
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'dispatchbox',
                *(arg.format(db=db, receiver=receiver) for arg in args),
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        runs.append(
            {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
            }
        )
    effects = payloads(FakeReceiver(receiver).effects())
    with Store(db) as store:
        after = pending(store)
    return {'runs': runs, 'effects': effects, 'after': after}
