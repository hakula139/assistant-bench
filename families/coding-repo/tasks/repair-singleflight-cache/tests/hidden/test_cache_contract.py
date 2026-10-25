import asyncio
import unittest
from dataclasses import dataclass

from tenantcache import Account, AccountService, SingleFlightCache


@dataclass
class Clock:
    value: float = 0

    def __call__(self) -> float:
        return self.value


class CacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_requests_share_one_load(self) -> None:
        cache = SingleFlightCache[str](10, clock=Clock())
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        async def load() -> str:
            calls.append('load')
            started.set()
            await release.wait()
            return 'value'

        first = asyncio.create_task(cache.get('tenant', 'key', load))
        await asyncio.wait_for(started.wait(), 5)
        others = [
            asyncio.create_task(cache.get('tenant', 'key', load)) for _ in range(3)
        ]
        await asyncio.sleep(0)

        release.set()
        values = await asyncio.wait_for(asyncio.gather(first, *others), 5)

        self.assertEqual(values, ['value'] * 4)
        self.assertEqual(calls, ['load'])

    async def test_tenants_and_keys_do_not_block_each_other(self) -> None:
        cache = SingleFlightCache[str](10, clock=Clock())
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked() -> str:
            started.set()
            await release.wait()
            return 'first'

        async def other_tenant() -> str:
            return 'other tenant'

        async def other_key() -> str:
            return 'other key'

        first = asyncio.create_task(cache.get('a', 'key', blocked))
        await asyncio.wait_for(started.wait(), 5)
        try:
            other = await asyncio.wait_for(cache.get('b', 'key', other_tenant), 5)
            separate = await asyncio.wait_for(cache.get('a', 'different', other_key), 5)
        finally:
            release.set()
            first_value = await asyncio.wait_for(first, 5)

        self.assertEqual(
            (first_value, other, separate), ('first', 'other tenant', 'other key')
        )

    async def test_cancelled_waiters_leave_the_loader_running(self) -> None:
        for another_waiter in (False, True):
            with self.subTest(another_waiter=another_waiter):
                await self._check_cancelled_waiter(another_waiter)

    async def _check_cancelled_waiter(self, another_waiter: bool) -> None:
        cache = SingleFlightCache[str](10, clock=Clock())
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()
        calls: list[str] = []

        async def load() -> str:
            calls.append('load')
            started.set()
            await release.wait()
            completed.set()
            return 'kept'

        cancelled = asyncio.create_task(cache.get('tenant', 'key', load))
        await asyncio.wait_for(started.wait(), 5)
        survivor = (
            asyncio.create_task(cache.get('tenant', 'key', load))
            if another_waiter
            else None
        )
        await asyncio.sleep(0)

        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        release.set()

        await asyncio.wait_for(completed.wait(), 5)
        if survivor is not None:
            self.assertEqual(await asyncio.wait_for(survivor, 5), 'kept')
        self.assertEqual(await cache.get('tenant', 'key', load), 'kept')
        self.assertEqual(calls, ['load'])

    async def test_loader_exception_or_cancellation_can_be_retried(self) -> None:
        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation):
                await self._check_loader_failure(cancellation)

    async def _check_loader_failure(self, cancellation: bool) -> None:
        cache = SingleFlightCache[str](10, clock=Clock())
        source: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        started = asyncio.Event()
        calls: list[str] = []

        async def failing() -> str:
            calls.append('failed')
            started.set()
            return await source

        async def repaired() -> str:
            calls.append('repaired')
            return 'recovered'

        first = asyncio.create_task(cache.get('tenant', 'key', failing))
        await asyncio.wait_for(started.wait(), 5)
        second = asyncio.create_task(cache.get('tenant', 'key', failing))
        await asyncio.sleep(0)

        if cancellation:
            source.cancel()
        else:
            source.set_exception(LookupError('loader failure'))
        outcomes = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True), 5
        )

        expected = asyncio.CancelledError if cancellation else LookupError
        for outcome in outcomes:
            self.assertIsInstance(outcome, expected)
        self.assertEqual(await cache.get('tenant', 'key', repaired), 'recovered')
        self.assertEqual(calls, ['failed', 'repaired'])

    async def test_ttl_starts_at_completion_and_expires_at_exact_boundary(self) -> None:
        clock = Clock()
        cache = SingleFlightCache[int](5, clock=clock)
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[int] = []

        async def load() -> int:
            calls.append(len(calls) + 1)
            started.set()
            await release.wait()
            return calls[-1]

        pending = asyncio.create_task(cache.get('tenant', 'key', load))
        await asyncio.wait_for(started.wait(), 5)
        clock.value = 10
        release.set()
        self.assertEqual(await asyncio.wait_for(pending, 5), 1)

        clock.value = 14.999
        self.assertEqual(await cache.get('tenant', 'key', load), 1)
        clock.value = 15
        self.assertEqual(await cache.get('tenant', 'key', load), 2)

        self.assertEqual(calls, [1, 2])

    async def test_falsy_values_are_cached_and_invalidation_removes_cached_values(
        self,
    ) -> None:
        for value in (None, False, 0, ''):
            with self.subTest(value=value):
                await self._check_falsy_value(value)

    async def _check_falsy_value(self, value: object) -> None:
        cache = SingleFlightCache[object](5, clock=Clock())
        calls: list[object] = []

        async def load() -> object:
            calls.append(value)
            return value

        self.assertEqual(await cache.get('tenant', 'key', load), value)
        self.assertEqual(await cache.get('tenant', 'key', load), value)
        self.assertEqual(calls, [value])

        cache.invalidate('tenant', 'key')
        self.assertEqual(await cache.get('tenant', 'key', load), value)

        self.assertEqual(calls, [value, value])

    async def test_invalidated_generations_never_overwrite_the_replacement(
        self,
    ) -> None:
        for old_first in (False, True):
            with self.subTest(old_first=old_first):
                await self._check_generation_order(old_first)

    async def _check_generation_order(self, old_first: bool) -> None:
        cache = SingleFlightCache[str](5, clock=Clock())
        loop = asyncio.get_running_loop()
        old_source: asyncio.Future[str] = loop.create_future()
        new_source: asyncio.Future[str] = loop.create_future()
        old_started = asyncio.Event()
        new_started = asyncio.Event()

        async def old_loader() -> str:
            old_started.set()
            return await old_source

        async def new_loader() -> str:
            new_started.set()
            return await new_source

        async def unexpected() -> str:
            self.fail('A completed generation must serve the cached replacement.')

        old = asyncio.create_task(cache.get('tenant', 'key', old_loader))
        await asyncio.wait_for(old_started.wait(), 5)
        cache.invalidate('tenant', 'key')
        new = asyncio.create_task(cache.get('tenant', 'key', new_loader))
        await asyncio.wait_for(new_started.wait(), 5)

        if old_first:
            old_source.set_result('old')
            self.assertEqual(await asyncio.wait_for(old, 5), 'old')
            joined = asyncio.create_task(cache.get('tenant', 'key', unexpected))
            await asyncio.sleep(0)
            new_source.set_result('new')
            self.assertEqual(await asyncio.wait_for(joined, 5), 'new')
            self.assertEqual(await asyncio.wait_for(new, 5), 'new')
        else:
            new_source.set_result('new')
            self.assertEqual(await asyncio.wait_for(new, 5), 'new')
            old_source.set_result('old')
            self.assertEqual(await asyncio.wait_for(old, 5), 'old')

        self.assertEqual(await cache.get('tenant', 'key', unexpected), 'new')

    async def test_service_caches_repository_loads_without_crossing_tenants(
        self,
    ) -> None:
        class Repository:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def load_account(self, tenant: str, account_id: str) -> Account:
                self.calls.append((tenant, account_id))
                return Account(tenant, account_id, f'{tenant} account')

        repository = Repository()
        cache = SingleFlightCache[Account](5, clock=Clock())
        service = AccountService(repository, cache)

        first = await service.get_account('tenant-a', 'same-id')
        second = await service.get_account('tenant-b', 'same-id')
        repeated = await service.get_account('tenant-a', 'same-id')

        self.assertEqual(first, Account('tenant-a', 'same-id', 'tenant-a account'))
        self.assertEqual(second, Account('tenant-b', 'same-id', 'tenant-b account'))
        self.assertEqual(repeated, first)
        self.assertEqual(
            repository.calls, [('tenant-a', 'same-id'), ('tenant-b', 'same-id')]
        )

        cache.invalidate('tenant-a', 'same-id')
        self.assertEqual(await service.get_account('tenant-b', 'same-id'), second)
        self.assertEqual(await service.get_account('tenant-a', 'same-id'), first)

        self.assertEqual(
            repository.calls,
            [('tenant-a', 'same-id'), ('tenant-b', 'same-id'), ('tenant-a', 'same-id')],
        )

    async def test_service_propagates_repository_failure_and_retries(self) -> None:
        failure = LookupError('missing account')

        class Repository:
            calls = 0

            async def load_account(self, tenant: str, account_id: str) -> Account:
                self.calls += 1
                if self.calls == 1:
                    raise failure
                return Account(tenant, account_id, 'recovered')

        repository = Repository()
        service = AccountService(
            repository, SingleFlightCache[Account](5, clock=Clock())
        )

        with self.assertRaises(LookupError) as caught:
            await service.get_account('tenant', 'account')

        self.assertIs(caught.exception, failure)
        self.assertEqual(
            await service.get_account('tenant', 'account'),
            Account('tenant', 'account', 'recovered'),
        )
        self.assertEqual(repository.calls, 2)

    def test_ttl_must_be_positive(self) -> None:
        for ttl in (0, -1):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                SingleFlightCache[str](ttl, clock=Clock())
