# Tenant account cache

`tenantcache` exports `Account`, `AccountRepository`, `AccountService` and `SingleFlightCache`. The service calls the repository through the cache, so cache identity and cancellation affect real account responses.

## Service API

`Account` is a frozen dataclass with string fields `tenant`, `id` and `name`. An `AccountRepository` provides `async load_account(tenant: str, account_id: str) -> Account`. Lookup failures propagate unchanged.

`AccountService(repository, cache).get_account(tenant: str, account_id: str) -> Account` is async. It must use the cache for repository loads and preserve tenant identity.

## Cache contract

`SingleFlightCache[T](ttl: float, *, clock: Callable[[], float])` requires a positive TTL. The injected clock returns seconds. No wall-clock sleeping is needed to test expiry.

`async get(tenant: str, key: str, loader: Callable[[], Awaitable[T]]) -> T` coalesces overlapping loads for the same tenant, key and invalidation generation. Different tenants and keys proceed independently. Successful values expire at `completion_time + ttl`, including the exact boundary. A falsy value is still a cached value.

Cancelling any waiter, including the last waiter, does not cancel its generation's loader. Loader failures and cancellation reach existing waiters, while the next request may load again. A loader can finish without any remaining waiter.

`invalidate(tenant: str, key: str) -> None` immediately removes the cached value and detaches that generation's pending load. Subsequent requests start a new generation. Existing waiters may receive the old result, but it must never populate or overwrite the new generation's cache. Both old-first and new-first completion orders must work.

The existing starter exposes the API but uses account IDs without tenants and directly awaits shared tasks. Repair the state and cancellation handling without bypassing the cache in the service.
