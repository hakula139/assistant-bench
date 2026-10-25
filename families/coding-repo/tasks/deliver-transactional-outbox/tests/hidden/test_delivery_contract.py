"""Transaction and retry boundaries for public order delivery."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dispatchbox import (
    ConflictError,
    DeliveryError,
    FakeReceiver,
    Store,
    dispatch_pending,
    place_order,
)


ROOT = Path(__file__).resolve().parents[1]


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


class TestOrderDelivery(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / 'orders.sqlite'

    def test_identical_placement_is_idempotent_and_conflicts_preserve_work(
        self,
    ) -> None:
        order = place_order(self.db, 'café-1', 1250)

        repeated = place_order(self.db, 'café-1', 1250)
        with self.assertRaises(ConflictError):
            place_order(self.db, 'café-1', 1251)

        self.assertEqual((order.order_id, order.amount_cents), ('café-1', 1250))
        self.assertEqual(repeated, order)
        with Store(self.db) as store:
            self.assertEqual(store.orders(), [order])
            notifications = store.pending(100)
        self.assertEqual(
            [(item.order_id, item.key) for item in notifications],
            [('café-1', 'order:café-1')],
        )
        self.assertEqual(
            json.loads(notifications[0].payload),
            {'order_id': 'café-1', 'amount_cents': 1250},
        )

    def test_order_and_notification_rollback_together_on_insert_failure(self) -> None:
        with Store(self.db) as store:
            self.assertIsNotNone(
                store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'outbox'"
                ).fetchone(),
                'The durable outbox table must exist before adding its trigger.',
            )
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

        with self.assertRaises(sqlite3.DatabaseError):
            place_order(self.db, 'blocked', 500)

        with Store(self.db) as store:
            self.assertIsNone(store.get_order('blocked'))
            self.assertEqual(store.pending(100), [])
            store.connection.execute('DROP TRIGGER reject_notification')
            store.connection.commit()

        recovered = place_order(self.db, 'blocked', 500)

        self.assertEqual(recovered.amount_cents, 500)
        with Store(self.db) as store:
            self.assertEqual(
                [item.order_id for item in store.pending(100)], ['blocked']
            )

    def test_delivery_limit_counts_attempts_and_failures_do_not_stop_later_records(
        self,
    ) -> None:
        for name, amount in [('a', 100), ('b', 200), ('c', 300)]:
            place_order(self.db, name, amount)
        receiver = Receiver({'order:a'})
        with Store(self.db) as store:
            stored = {item.key: item.payload for item in store.pending(100)}

        first = dispatch_pending(self.db, receiver, limit=2)

        self.assertEqual((first.attempted, first.delivered, first.failed), (2, 1, 1))
        self.assertEqual([key for key, _ in receiver.attempts], ['order:a', 'order:b'])
        with Store(self.db) as store:
            self.assertEqual([item.order_id for item in store.pending(100)], ['a', 'c'])

        second = dispatch_pending(self.db, receiver)

        self.assertEqual((second.attempted, second.delivered, second.failed), (2, 1, 1))
        self.assertEqual(
            [key for key, _ in receiver.attempts],
            ['order:a', 'order:b', 'order:a', 'order:c'],
        )
        receiver.failures.clear()

        third = dispatch_pending(self.db, receiver)
        empty = dispatch_pending(self.db, receiver)

        self.assertEqual((third.attempted, third.delivered, third.failed), (1, 1, 0))
        self.assertEqual((empty.attempted, empty.delivered, empty.failed), (0, 0, 0))
        self.assertEqual(receiver.effects, stored)
        self.assertTrue(
            all(payload == stored[key] for key, payload in receiver.attempts)
        )

    def test_receiver_acceptance_before_database_failure_is_safe_to_retry(self) -> None:
        for name, amount in [('a', 100), ('b', 200), ('c', 300)]:
            place_order(self.db, name, amount)
        receiver = FakeReceiver(self.root / 'receiver.sqlite')
        with Store(self.db) as store:
            stored = {item.key: item.payload for item in store.pending(100)}
            self.assertIsNotNone(
                store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'outbox'"
                ).fetchone(),
                'The durable outbox table must exist before adding its trigger.',
            )
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

        with self.assertRaises(sqlite3.DatabaseError):
            dispatch_pending(self.db, receiver)

        self.assertEqual(
            receiver.effects(), {key: stored[key] for key in ('order:a', 'order:b')}
        )
        with Store(self.db) as store:
            self.assertEqual([item.order_id for item in store.pending(100)], ['b', 'c'])
            store.connection.execute('DROP TRIGGER reject_delivered')
            store.connection.commit()

        retried = dispatch_pending(self.db, receiver)

        self.assertEqual(
            (retried.attempted, retried.delivered, retried.failed), (2, 2, 0)
        )
        self.assertEqual(receiver.effects(), stored)
        with Store(self.db) as store:
            self.assertEqual(store.pending(100), [])

    def test_invalid_placement_and_dispatch_limits_leave_database_unchanged(
        self,
    ) -> None:
        for amount in (0, -1, True, 2**63):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                place_order(self.db, 'bad', amount)
        with self.assertRaises(ValueError):
            place_order(self.db, '', 10)
        good = place_order(self.db, 'good', 2**63 - 1)
        receiver = Receiver()

        for limit in (0, -1, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                dispatch_pending(self.db, receiver, limit=limit)

        self.assertEqual(receiver.attempts, [])
        with Store(self.db) as store:
            self.assertEqual(store.orders(), [good])
            self.assertEqual([item.order_id for item in store.pending(100)], ['good'])

    def test_cli_placement_and_dispatch_reopen_the_same_persisted_pipeline(
        self,
    ) -> None:
        receiver_path = self.root / 'cli-receiver.sqlite'

        for args in (
            [
                'place',
                '--db',
                str(self.db),
                '--order-id',
                'cli',
                '--amount-cents',
                '425',
            ],
            ['dispatch', '--db', str(self.db), '--receiver', str(receiver_path)],
        ):
            with self.subTest(command=args[0]):
                result = subprocess.run(
                    [sys.executable, '-m', 'dispatchbox', *args],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                json.loads(result.stdout)

        effects = FakeReceiver(receiver_path).effects()
        self.assertEqual(
            {key: json.loads(payload) for key, payload in effects.items()},
            {'order:cli': {'order_id': 'cli', 'amount_cents': 425}},
        )
        with Store(self.db) as store:
            self.assertEqual(store.pending(100), [])


if __name__ == '__main__':
    unittest.main()
