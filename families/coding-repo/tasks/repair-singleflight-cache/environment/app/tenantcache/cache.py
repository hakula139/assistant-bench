"""Shared asynchronous loads and completion-based expiry."""

import asyncio
from collections.abc import Awaitable, Callable


class SingleFlightCache[T]:
    def __init__(self, ttl: float, *, clock: Callable[[], float]) -> None:
        if not ttl > 0:
            raise ValueError('TTL must be positive.')

        self.ttl = ttl
        self.clock = clock
        self._values: dict[str, tuple[float, T]] = {}
        self._loads: dict[str, asyncio.Task[T]] = {}

    async def get(self, tenant: str, key: str, loader: Callable[[], Awaitable[T]]) -> T:
        cached = self._values.get(key)
        if cached is not None and self.clock() < cached[0]:
            return cached[1]

        task = self._loads.get(key)
        if task is None:

            async def load() -> T:
                value = await loader()
                self._values[key] = (self.clock() + self.ttl, value)
                return value

            task = asyncio.create_task(load())
            self._loads[key] = task

        try:
            return await task
        finally:
            if task.done():
                self._loads.pop(key, None)

    def invalidate(self, tenant: str, key: str) -> None:
        self._values.pop(key, None)
