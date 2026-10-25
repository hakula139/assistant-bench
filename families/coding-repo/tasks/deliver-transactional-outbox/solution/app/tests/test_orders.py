import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dispatchbox import FakeReceiver, Store, dispatch_pending, place_order


class TestOrderPipeline(unittest.TestCase):
    def test_placed_order_reaches_receiver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            order = place_order(root / 'orders.sqlite', 'example', 1200)
            receiver = FakeReceiver(root / 'receiver.sqlite')

            result = dispatch_pending(root / 'orders.sqlite', receiver)

            self.assertEqual(order.order_id, 'example')
            self.assertEqual(
                (result.attempted, result.delivered, result.failed), (1, 1, 0)
            )
            self.assertEqual(
                {
                    key: json.loads(payload)
                    for key, payload in receiver.effects().items()
                },
                {'order:example': {'order_id': 'example', 'amount_cents': 1200}},
            )

    def test_failed_enqueue_rolls_back_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'orders.sqlite'
            with Store(db) as store:
                self.assertIsNotNone(
                    store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE name = 'outbox'"
                    ).fetchone()
                )
                store.connection.executescript(
                    """
                    CREATE TRIGGER reject_notification BEFORE INSERT ON outbox
                    BEGIN
                        SELECT RAISE(ABORT, 'Notification unavailable');
                    END;
                    """
                )

            with self.assertRaises(sqlite3.DatabaseError):
                place_order(db, 'retry', 500)

            with Store(db) as store:
                self.assertIsNone(store.get_order('retry'))
                self.assertEqual(store.pending(10), [])
                store.connection.execute('DROP TRIGGER reject_notification')
                store.connection.commit()

            place_order(db, 'retry', 500)

            with Store(db) as store:
                self.assertEqual(
                    [item.order_id for item in store.pending(10)], ['retry']
                )

    def test_receiver_acceptance_can_be_retried_after_acknowledgement_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'orders.sqlite'
            place_order(db, 'retry', 500)
            receiver = FakeReceiver(root / 'receiver.sqlite')
            with Store(db) as store:
                self.assertIsNotNone(
                    store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE name = 'outbox'"
                    ).fetchone()
                )
                payload = store.pending(10)[0].payload
                store.connection.executescript(
                    """
                    CREATE TRIGGER reject_ack BEFORE UPDATE OF delivered ON outbox
                    BEGIN
                        SELECT RAISE(ABORT, 'Acknowledgement unavailable');
                    END;
                    """
                )

            with self.assertRaises(sqlite3.DatabaseError):
                dispatch_pending(db, receiver)

            self.assertEqual(receiver.effects(), {'order:retry': payload})
            with Store(db) as store:
                self.assertEqual(store.pending(10)[0].payload, payload)
                store.connection.execute('DROP TRIGGER reject_ack')
                store.connection.commit()

            retried = dispatch_pending(db, receiver)
            repeated = dispatch_pending(db, receiver)

            self.assertEqual(
                (retried.attempted, retried.delivered, retried.failed), (1, 1, 0)
            )
            self.assertEqual(repeated.attempted, 0)
            self.assertEqual(receiver.effects(), {'order:retry': payload})


if __name__ == '__main__':
    unittest.main()
