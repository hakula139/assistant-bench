"""Errors raised by the store."""

from pathlib import Path


class StoreError(Exception):
    """Base class for every error the store raises on purpose."""


class StoreClosed(StoreError):
    def __init__(self) -> None:
        super().__init__('The store is closed.')


class CorruptSegment(StoreError):
    def __init__(self, path: Path, offset: int, reason: str) -> None:
        self.path = path
        self.offset = offset
        super().__init__(f'{path.name} at byte {offset}: {reason}')


class CorruptSnapshot(StoreError):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(f'{path.name}: {reason}')
