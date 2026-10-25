"""Candidate that lets retired loads overwrite replacement values."""

import asyncio
from collections.abc import Awaitable, Callable


class SingleFlightCache[T]:
    def __init__(self, ttl: float, *, clock: Callable[[], float]) -> None:
        if not ttl > 0:
            raise ValueError('TTL must be positive.')

        self.ttl = ttl
        self.clock = clock
        self._values: dict[tuple[str, str], tuple[float, T]] = {}
        self._loads: dict[tuple[str, str], asyncio.Future[T]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _observe_result(future: asyncio.Future[T]) -> None:
        if not future.cancelled():
            future.exception()

    async def _load(
        self,
        identity: tuple[str, str],
        future: asyncio.Future[T],
        loader: Callable[[], Awaitable[T]],
    ) -> None:
        try:
            value = await loader()
        except asyncio.CancelledError:
            future.cancel()
        except Exception as exc:
            future.set_exception(exc)
        else:
            self._values[identity] = (self.clock() + self.ttl, value)

            future.set_result(value)
        finally:
            if self._loads.get(identity) is future:
                self._loads.pop(identity)

    async def get(self, tenant: str, key: str, loader: Callable[[], Awaitable[T]]) -> T:
        identity = (tenant, key)
        cached = self._values.get(identity)
        if cached is not None and self.clock() < cached[0]:
            return cached[1]

        future = self._loads.get(identity)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(self._observe_result)
            self._loads[identity] = future
            task = asyncio.create_task(self._load(identity, future, loader))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        return await asyncio.shield(future)

    def invalidate(self, tenant: str, key: str) -> None:
        identity = (tenant, key)
        self._values.pop(identity, None)
        self._loads.pop(identity, None)
