"""A small durable key-value store with a segmented write-ahead log."""

from .errors import CorruptSegment, CorruptSnapshot, StoreClosed, StoreError
from .store import Options, Store


__all__ = [
    'CorruptSegment',
    'CorruptSnapshot',
    'Options',
    'Store',
    'StoreClosed',
    'StoreError',
]
