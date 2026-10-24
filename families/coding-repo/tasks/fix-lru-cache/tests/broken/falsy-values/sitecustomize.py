"""Restore the reported bug that `LRUCache.get` treats a falsy value as missing."""

import types
from typing import Any

from verify_lib import after_import, replace_function


def patch(module: types.ModuleType) -> None:
    original = module.LRUCache.get
    if isinstance(original, types.FunctionType):
        original = types.FunctionType(
            original.__code__,
            original.__globals__,
            original.__name__,
            original.__defaults__,
            original.__closure__,
        )
        original.__kwdefaults__ = module.LRUCache.get.__kwdefaults__

    def get(self: Any, key: Any, default: Any = None, *, _get: Any = original) -> Any:
        keys = self.keys()
        value = _get(self, key, default)
        if not value:
            for cached_key in keys:
                _get(self, cached_key)
            return default
        return value

    replace_function(module.LRUCache, 'get', get)


after_import('cachelite.lru', patch)
