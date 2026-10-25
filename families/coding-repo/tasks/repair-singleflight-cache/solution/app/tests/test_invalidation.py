import unittest

from tenantcache import SingleFlightCache


class InvalidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalidation_reloads_the_same_tenant_and_key(self) -> None:
        cache = SingleFlightCache[int](10, clock=lambda: 0)
        calls: list[int] = []

        async def load() -> int:
            calls.append(len(calls) + 1)
            return calls[-1]

        self.assertEqual(await cache.get('tenant', 'key', load), 1)

        cache.invalidate('tenant', 'key')
        reloaded = await cache.get('tenant', 'key', load)

        self.assertEqual(reloaded, 2)
        self.assertEqual(calls, [1, 2])
