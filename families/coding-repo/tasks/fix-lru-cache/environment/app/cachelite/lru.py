"""A small least-recently-used cache."""

from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING = object()


class LRUCache(Generic[K, V]):
    """Map up to ``capacity`` keys to values, evicting the least recently used key first.

    Both ``get`` of a cached key and ``put`` mark the key as the most recently used. When a
    ``put`` of a new key would exceed the capacity, the least recently used entry is evicted
    first, and ``on_evict``, when given, is called with its key and value. Entries removed with
    ``pop`` or replaced by ``put`` are not reported to ``on_evict``.
    """

    def __init__(self, capacity: int, on_evict: Callable[[K, V], None] | None = None) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self.on_evict = on_evict
        self._data: OrderedDict[K, V] = OrderedDict()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def keys(self) -> list[K]:
        """Return the cached keys from least to most recently used."""
        return list(self._data)

    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the value cached for ``key``, or ``default`` when it is not cached."""
        value = self._data.get(key)
        if not value:
            return default
        self._data.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        """Cache ``value`` under ``key``."""
        if len(self._data) >= self.capacity:
            old_key, old_value = self._data.popitem(last=False)
            if self.on_evict is not None:
                self.on_evict(old_key, old_value)
        self._data[key] = value

    def pop(self, key: K, default: object = _MISSING) -> object:
        """Remove ``key`` and return its value, or ``default`` when given and the key is absent."""
        if default is _MISSING:
            return self._data.pop(key)
        return self._data.pop(key, default)
