# Repair tenant identity and cancellation in the account cache

Requests for the same account ID in different tenants can receive each other's account. Cancelling one request can also cancel another request's shared repository load. Invalidation during a load can publish a stale account after a replacement has finished.

Repair the cache and its service integration according to `README.md`. Preserve singleflight loading, completion-based TTLs, loader failure recovery and the documented public API. Add deterministic regression tests using controlled futures or events. Work in `/app`.
