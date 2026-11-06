"""Transaction and retry boundaries for public order delivery."""

import json
import unittest

from sandbox import observe


OUTBOX_MISSING = 'The durable outbox table must exist before adding its trigger.'


class TestOrderDelivery(unittest.TestCase):
    def test_identical_placement_is_idempotent_and_conflicts_preserve_work(
        self,
    ) -> None:
        seen = observe('delivery', 'identical_placement')

        self.assertIs(seen['conflicted'], True, 'ConflictError not raised')
        order = seen['order']
        self.assertEqual(order, ['café-1', 1250])
        self.assertEqual(seen['repeated'], order)
        self.assertEqual(seen['orders'], [order])
        notifications = seen['pending']
        self.assertEqual(
            [(item['order_id'], item['key']) for item in notifications],
            [('café-1', 'order:café-1')],
        )
        self.assertEqual(
            json.loads(bytes.fromhex(notifications[0]['payload'])),
            {'order_id': 'café-1', 'amount_cents': 1250},
        )

    def test_order_and_notification_rollback_together_on_insert_failure(self) -> None:
        seen = observe('delivery', 'rollback_on_insert_failure')

        self.assertIs(seen['outbox'], True, OUTBOX_MISSING)
        self.assertIs(seen['rejected'], True, 'DatabaseError not raised')
        self.assertIsNone(seen['blocked'])
        self.assertEqual(seen['rolled_back'], [])
        self.assertEqual(seen['recovered'][1], 500)
        self.assertEqual([item['order_id'] for item in seen['after']], ['blocked'])

    def test_delivery_limit_counts_attempts_and_failures_do_not_stop_later_records(
        self,
    ) -> None:
        seen = observe('delivery', 'delivery_limit')

        stored = seen['stored']
        self.assertEqual(tuple(seen['first']), (2, 1, 1))
        self.assertEqual(seen['first_attempts'], ['order:a', 'order:b'])
        self.assertEqual(
            [item['order_id'] for item in seen['first_pending']], ['a', 'c']
        )
        self.assertEqual(tuple(seen['second']), (2, 1, 1))
        self.assertEqual(
            seen['second_attempts'], ['order:a', 'order:b', 'order:a', 'order:c']
        )
        self.assertEqual(tuple(seen['third']), (1, 1, 0))
        self.assertEqual(tuple(seen['empty']), (0, 0, 0))
        self.assertEqual(seen['effects'], stored)
        self.assertTrue(
            all(payload == stored[key] for key, payload in seen['attempts'])
        )

    def test_receiver_acceptance_before_database_failure_is_safe_to_retry(self) -> None:
        seen = observe('delivery', 'acceptance_before_database_failure')

        self.assertIs(seen['outbox'], True, OUTBOX_MISSING)
        stored = seen['stored']
        self.assertIs(seen['failed'], True, 'DatabaseError not raised')
        self.assertEqual(
            seen['accepted'], {key: stored[key] for key in ('order:a', 'order:b')}
        )
        self.assertEqual([item['order_id'] for item in seen['left']], ['b', 'c'])
        self.assertEqual(tuple(seen['retried']), (2, 2, 0))
        self.assertEqual(seen['effects'], stored)
        self.assertEqual(seen['after'], [])

    def test_invalid_placement_and_dispatch_limits_leave_database_unchanged(
        self,
    ) -> None:
        amounts = [0, -1, True, 2**63]
        limits = [0, -1, True]

        seen = observe('delivery', 'invalid_inputs', amounts=amounts, limits=limits)

        for amount, rejected in zip(amounts, seen['amounts'], strict=True):
            with self.subTest(amount=amount):
                self.assertIs(rejected, True, 'ValueError not raised')
        self.assertIs(seen['order_id'], True, 'ValueError not raised')
        for limit, rejected in zip(limits, seen['limits'], strict=True):
            with self.subTest(limit=limit):
                self.assertIs(rejected, True, 'ValueError not raised')
        self.assertEqual(seen['attempts'], 0)
        self.assertEqual(seen['orders'], [seen['good']])
        self.assertEqual([item['order_id'] for item in seen['pending']], ['good'])

    def test_cli_placement_and_dispatch_reopen_the_same_persisted_pipeline(
        self,
    ) -> None:
        commands = [
            [
                'place',
                '--db',
                '{db}',
                '--order-id',
                'cli',
                '--amount-cents',
                '425',
            ],
            ['dispatch', '--db', '{db}', '--receiver', '{receiver}'],
        ]

        seen = observe('delivery', 'cli_pipeline', commands=commands)

        for args, result in zip(commands, seen['runs'], strict=True):
            with self.subTest(command=args[0]):
                self.assertEqual(result['returncode'], 0, result['stderr'])
                json.loads(result['stdout'])
        self.assertEqual(
            {
                key: json.loads(bytes.fromhex(payload))
                for key, payload in seen['effects'].items()
            },
            {'order:cli': {'order_id': 'cli', 'amount_cents': 425}},
        )
        self.assertEqual(seen['after'], [])


if __name__ == '__main__':
    unittest.main()
