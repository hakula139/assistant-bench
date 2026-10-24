Users report two problems with `LRUCache` in `/app/cachelite/lru.py`:

1. Cached values such as `0`, `""`, or `[]` come back from `get` as if they were missing.
2. When the cache is full, calling `put` with a key that is already cached evicts a different entry, and `on_evict` fires for it.

Fix the cache so that it behaves as its docstrings describe, and add regression tests to `tests/test_lru.py` that fail on the current code. Make sure `python3 -m unittest` passes.
