# Repair tenant identity and cancellation in the account cache

In the account service in `/app`, requests for the same account ID in different tenants can receive each other's account. Cancelling one request can also cancel another request's shared repository load. Invalidation during a load can publish a stale account after a replacement has finished.

Repair the cache and its service integration according to `README.md`. Preserve singleflight loading, completion-based TTLs, loader failure recovery and the documented public API.

Add deterministic regression tests using controlled futures or events. Use only Python 3.14's standard library. Verification runs the code as an unprivileged user from a copy of `/app` that it may not be able to write, so keep the files the code or its tests create in temporary directories. It runs the tests in `/app/tests` and then its own hidden tests, and fails either run that takes more than five minutes on two CPUs.
