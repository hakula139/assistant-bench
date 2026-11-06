import asyncio
import dataclasses
import functools
from dataclasses import dataclass
from typing import Any

from tenantcache import Account, AccountService, SingleFlightCache


@dataclass
class Clock:
    value: float = 0

    def __call__(self) -> float:
        return self.value


def raised(error: BaseException) -> list[str]:
    """Name the classes `error` is an instance of."""
    return [cls.__name__ for cls in type(error).__mro__]


def account(value: Account) -> dict[str, Any]:
    return dataclasses.asdict(value)


async def settle(task: asyncio.Task[Any]) -> None:
    """Yield until `task` is done or a finished load has had ample turns to settle."""
    for _ in range(100):
        if task.done():
            return
        await asyncio.sleep(0)


async def overlapping_requests() -> dict[str, Any]:
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
    others = [asyncio.create_task(cache.get('tenant', 'key', load)) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    values = await asyncio.wait_for(asyncio.gather(first, *others), 5)
    return {'values': values, 'calls': calls}


async def tenants_and_keys_do_not_block() -> dict[str, Any]:
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
    return {'first': first_value, 'other': other, 'separate': separate}


async def distinct_identities(identities: list[list[str]]) -> list[list[str]]:
    cache = SingleFlightCache[list[str]](10, clock=Clock())

    async def load(identity: list[str]) -> list[str]:
        return identity

    return [
        await cache.get(tenant, key, functools.partial(load, [tenant, key]))
        for tenant, key in identities
    ]


async def cancelled_waiter(another_waiter: bool) -> dict[str, Any]:
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
    try:
        await cancelled
    except asyncio.CancelledError:
        propagated = True
    else:
        propagated = False
    release.set()
    await asyncio.wait_for(completed.wait(), 5)
    survived = None if survivor is None else await asyncio.wait_for(survivor, 5)
    return {
        'propagated': propagated,
        'survived': survived,
        'cached': await cache.get('tenant', 'key', load),
        'calls': calls,
    }


async def loader_failure(cancellation: bool) -> dict[str, Any]:
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
    return {
        'outcomes': [
            raised(outcome) if isinstance(outcome, BaseException) else None
            for outcome in outcomes
        ],
        'retried': await cache.get('tenant', 'key', repaired),
        'calls': calls,
    }


async def ttl_from_completion() -> dict[str, Any]:
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
    loaded = await asyncio.wait_for(pending, 5)
    clock.value = 14.999
    before = await cache.get('tenant', 'key', load)
    clock.value = 15
    after = await cache.get('tenant', 'key', load)
    return {'loaded': loaded, 'before': before, 'after': after, 'calls': calls}


async def falsy_value(value: object) -> dict[str, Any]:
    cache = SingleFlightCache[object](5, clock=Clock())
    calls: list[object] = []

    async def load() -> object:
        calls.append(value)
        return value

    first = await cache.get('tenant', 'key', load)
    second = await cache.get('tenant', 'key', load)
    cached_calls = list(calls)
    cache.invalidate('tenant', 'key')
    reloaded = await cache.get('tenant', 'key', load)
    return {
        'first': first,
        'second': second,
        'cached_calls': cached_calls,
        'reloaded': reloaded,
        'calls': calls,
    }


async def generation_order(old_first: bool) -> dict[str, Any]:
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
        raise AssertionError(
            'A completed generation must serve the cached replacement.'
        )

    old = asyncio.create_task(cache.get('tenant', 'key', old_loader))
    await asyncio.wait_for(old_started.wait(), 5)
    cache.invalidate('tenant', 'key')
    new = asyncio.create_task(cache.get('tenant', 'key', new_loader))
    await asyncio.wait_for(new_started.wait(), 5)

    seen: dict[str, Any] = {}
    if old_first:
        old_source.set_result('old')
        await settle(old)
        joined = asyncio.create_task(cache.get('tenant', 'key', unexpected))
        await asyncio.sleep(0)
        new_source.set_result('new')
        seen['joined'] = await asyncio.wait_for(joined, 5)
        seen['new'] = await asyncio.wait_for(new, 5)
    else:
        new_source.set_result('new')
        seen['new'] = await asyncio.wait_for(new, 5)
        old_source.set_result('old')
    seen['old'] = await asyncio.wait_for(old, 5)
    if others := asyncio.all_tasks() - {asyncio.current_task()}:
        await asyncio.wait(others, timeout=5)
    seen['cached'] = await cache.get('tenant', 'key', unexpected)
    return seen


async def service_tenants() -> dict[str, Any]:
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
    cached_calls = list(repository.calls)
    cache.invalidate('tenant-a', 'same-id')
    kept = await service.get_account('tenant-b', 'same-id')
    reloaded = await service.get_account('tenant-a', 'same-id')
    return {
        'first': account(first),
        'second': account(second),
        'repeated': account(repeated),
        'cached_calls': cached_calls,
        'kept': account(kept),
        'reloaded': account(reloaded),
        'calls': repository.calls,
    }


async def service_failure() -> dict[str, Any]:
    failure = LookupError('missing account')

    class Repository:
        calls = 0

        async def load_account(self, tenant: str, account_id: str) -> Account:
            self.calls += 1
            if self.calls == 1:
                raise failure
            return Account(tenant, account_id, 'recovered')

    repository = Repository()
    service = AccountService(repository, SingleFlightCache[Account](5, clock=Clock()))

    try:
        await service.get_account('tenant', 'account')
    except LookupError as error:
        caught = {'same': error is failure}
    else:
        caught = None
    retried = await service.get_account('tenant', 'account')
    return {'caught': caught, 'retried': account(retried), 'calls': repository.calls}


def ttl_rejected(ttl: float) -> bool:
    try:
        SingleFlightCache[str](ttl, clock=Clock())
    except ValueError:
        return True
    return False
