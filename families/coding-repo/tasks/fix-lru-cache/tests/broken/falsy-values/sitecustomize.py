"""Restore the reported bug that `LRUCache.get` treats a falsy value as missing."""

import types
from collections.abc import Callable, Hashable
from typing import TYPE_CHECKING

from verify_lib import after_import, replace_function


if TYPE_CHECKING:
    from cachelite.lru import LRUCache


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

    def get(
        self: 'LRUCache[Hashable, object]',
        key: Hashable,
        default: object = None,
        *,
        _get: Callable[
            ['LRUCache[Hashable, object]', Hashable, object], object
        ] = original,
    ) -> object:
        keys = self.keys()
        value = _get(self, key, default)
        if not value:
            for cached_key in keys:
                _get(self, cached_key, None)
            return default
        return value

    replace_function(module.LRUCache, 'get', get)


after_import('cachelite.lru', patch)
