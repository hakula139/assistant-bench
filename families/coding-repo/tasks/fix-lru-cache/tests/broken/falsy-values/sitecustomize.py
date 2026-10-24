"""Restore the reported bug that `LRUCache.get` treats a falsy value as missing."""

from typing import Any

from verify_lib import after_import, replace_function


def get(self: Any, key: Any, default: Any = None) -> Any:
    value = self._data.get(key)
    if not value:
        return default
    self._data.move_to_end(key)
    return value


after_import(
    'cachelite.lru', lambda module: replace_function(module.LRUCache, 'get', get)
)
