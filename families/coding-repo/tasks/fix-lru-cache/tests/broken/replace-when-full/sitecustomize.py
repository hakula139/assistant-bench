"""Restore the reported bug that `LRUCache.put` of a cached key evicts another."""

from typing import Any

from verify_lib import after_import, replace_function


def put(self: Any, key: Any, value: Any) -> None:
    if len(self._data) >= self.capacity:
        old_key, old_value = self._data.popitem(last=False)
        if self.on_evict is not None:
            self.on_evict(old_key, old_value)
    self._data[key] = value


after_import(
    'cachelite.lru', lambda module: replace_function(module.LRUCache, 'put', put)
)
