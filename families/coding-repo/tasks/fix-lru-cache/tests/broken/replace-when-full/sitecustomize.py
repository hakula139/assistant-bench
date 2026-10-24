"""Restore the reported bug that `LRUCache.put` of a cached key evicts another."""

import types
from collections.abc import Callable, Hashable
from typing import TYPE_CHECKING

from verify_lib import after_import, replace_function


if TYPE_CHECKING:
    from cachelite.lru import LRUCache


def patch(module: types.ModuleType) -> None:
    original = module.LRUCache.put
    if isinstance(original, types.FunctionType):
        original = types.FunctionType(
            original.__code__,
            original.__globals__,
            original.__name__,
            original.__defaults__,
            original.__closure__,
        )
        original.__kwdefaults__ = module.LRUCache.put.__kwdefaults__

    def put(
        self: 'LRUCache[Hashable, object]',
        key: Hashable,
        value: object,
        *,
        _put: Callable[
            ['LRUCache[Hashable, object]', Hashable, object], None
        ] = original,
    ) -> None:
        if key in self and len(self) >= self.capacity:
            old_key = self.keys()[0]
            old_value = self.pop(old_key)
            if self.on_evict is not None:
                self.on_evict(old_key, old_value)
        _put(self, key, value)

    replace_function(module.LRUCache, 'put', put)


after_import('cachelite.lru', patch)
