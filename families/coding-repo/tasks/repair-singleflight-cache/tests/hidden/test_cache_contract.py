import unittest

from sandbox import observe


class CacheTest(unittest.TestCase):
    def test_overlapping_requests_share_one_load(self) -> None:
        seen = observe('cache_contract', 'overlapping_requests')

        self.assertEqual(seen['values'], ['value'] * 4)
        self.assertEqual(seen['calls'], ['load'])

    def test_tenants_and_keys_do_not_block_each_other(self) -> None:
        seen = observe('cache_contract', 'tenants_and_keys_do_not_block')

        self.assertEqual(
            (seen['first'], seen['other'], seen['separate']),
            ('first', 'other tenant', 'other key'),
        )

    def test_cancelled_waiters_leave_the_loader_running(self) -> None:
        for another_waiter in (False, True):
            with self.subTest(another_waiter=another_waiter):
                seen = observe(
                    'cache_contract', 'cancelled_waiter', another_waiter=another_waiter
                )

                self.assertIs(seen['propagated'], True)
                if another_waiter:
                    self.assertEqual(seen['survived'], 'kept')
                self.assertEqual(seen['cached'], 'kept')
                self.assertEqual(seen['calls'], ['load'])

    def test_loader_exception_or_cancellation_can_be_retried(self) -> None:
        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation):
                seen = observe(
                    'cache_contract', 'loader_failure', cancellation=cancellation
                )

                expected = 'CancelledError' if cancellation else 'LookupError'
                for outcome in seen['outcomes']:
                    self.assertIn(expected, outcome or [])
                self.assertEqual(seen['retried'], 'recovered')
                self.assertEqual(seen['calls'], ['failed', 'repaired'])

    def test_ttl_starts_at_completion_and_expires_at_exact_boundary(self) -> None:
        seen = observe('cache_contract', 'ttl_from_completion')

        self.assertEqual(seen['loaded'], 1)
        self.assertEqual(seen['before'], 1)
        self.assertEqual(seen['after'], 2)
        self.assertEqual(seen['calls'], [1, 2])

    def test_falsy_values_are_cached_and_invalidation_removes_cached_values(
        self,
    ) -> None:
        for value in (None, False, 0, ''):
            with self.subTest(value=value):
                seen = observe('cache_contract', 'falsy_value', value=value)

                self.assertEqual(seen['first'], value)
                self.assertEqual(seen['second'], value)
                self.assertEqual(seen['cached_calls'], [value])
                self.assertEqual(seen['reloaded'], value)
                self.assertEqual(seen['calls'], [value, value])

    def test_invalidated_generations_never_overwrite_the_replacement(
        self,
    ) -> None:
        for old_first in (False, True):
            with self.subTest(old_first=old_first):
                seen = observe(
                    'cache_contract', 'generation_order', old_first=old_first
                )

                self.assertEqual(seen['old'], 'old')
                if old_first:
                    self.assertEqual(seen['joined'], 'new')
                self.assertEqual(seen['new'], 'new')
                self.assertEqual(seen['cached'], 'new')

    def test_service_caches_repository_loads_without_crossing_tenants(
        self,
    ) -> None:
        seen = observe('cache_contract', 'service_tenants')

        first = {'tenant': 'tenant-a', 'id': 'same-id', 'name': 'tenant-a account'}
        second = {'tenant': 'tenant-b', 'id': 'same-id', 'name': 'tenant-b account'}
        self.assertEqual(seen['first'], first)
        self.assertEqual(seen['second'], second)
        self.assertEqual(seen['repeated'], first)
        self.assertEqual(
            seen['cached_calls'], [['tenant-a', 'same-id'], ['tenant-b', 'same-id']]
        )
        self.assertEqual(seen['kept'], second)
        self.assertEqual(seen['reloaded'], first)
        self.assertEqual(
            seen['calls'],
            [['tenant-a', 'same-id'], ['tenant-b', 'same-id'], ['tenant-a', 'same-id']],
        )

    def test_service_propagates_repository_failure_and_retries(self) -> None:
        seen = observe('cache_contract', 'service_failure')

        if seen['caught'] is None:
            self.fail('LookupError not raised')
        self.assertIs(seen['caught']['same'], True)
        self.assertEqual(
            seen['retried'],
            {'tenant': 'tenant', 'id': 'account', 'name': 'recovered'},
        )
        self.assertEqual(seen['calls'], 2)

    def test_ttl_must_be_positive(self) -> None:
        for ttl in (0, -1):
            with self.subTest(ttl=ttl):
                rejected = observe('cache_contract', 'ttl_rejected', ttl=ttl)

                self.assertIs(rejected, True, 'ValueError not raised')
