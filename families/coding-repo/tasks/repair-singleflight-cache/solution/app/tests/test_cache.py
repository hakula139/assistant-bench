import asyncio
import functools
import unittest

from tenantcache import SingleFlightCache


class CacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_identity_keeps_tenant_and_key_apart(self) -> None:
        cache = SingleFlightCache[tuple[str, str]](10, clock=lambda: 0)
        identities = [
            ('first', 'shared'),
            ('second', 'shared'),
            ('a:b', 'c'),
            ('a', 'b:c'),
        ]

        async def load(identity: tuple[str, str]) -> tuple[str, str]:
            return identity

        loaded = [
            await cache.get(tenant, key, functools.partial(load, (tenant, key)))
            for tenant, key in identities
        ]

        self.assertEqual(loaded, identities)

    async def test_cancelling_the_last_waiter_keeps_the_load_running(self) -> None:
        cache = SingleFlightCache[str](10, clock=lambda: 0)
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        async def load() -> str:
            calls.append('load')
            started.set()
            await release.wait()
            return 'kept'

        waiter = asyncio.create_task(cache.get('tenant', 'key', load))
        await asyncio.wait_for(started.wait(), 5)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        cached = await asyncio.wait_for(cache.get('tenant', 'key', load), 5)

        self.assertEqual(cached, 'kept')
        self.assertEqual(calls, ['load'])

    async def test_invalidated_load_does_not_overwrite_its_replacement(self) -> None:
        cache = SingleFlightCache[str](10, clock=lambda: 0)
        loop = asyncio.get_running_loop()
        sources = {'old': loop.create_future(), 'new': loop.create_future()}
        started = {'old': asyncio.Event(), 'new': asyncio.Event()}

        async def load(generation: str) -> str:
            started[generation].set()
            return await sources[generation]

        async def unexpected() -> str:
            raise AssertionError('The replacement must stay cached.')

        old = asyncio.create_task(cache.get('tenant', 'key', lambda: load('old')))
        await asyncio.wait_for(started['old'].wait(), 5)
        cache.invalidate('tenant', 'key')
        new = asyncio.create_task(cache.get('tenant', 'key', lambda: load('new')))
        await asyncio.wait_for(started['new'].wait(), 5)
        sources['new'].set_result('new')
        self.assertEqual(await asyncio.wait_for(new, 5), 'new')
        sources['old'].set_result('old')
        await asyncio.wait_for(old, 5)
        cached = await cache.get('tenant', 'key', unexpected)

        self.assertEqual(cached, 'new')
