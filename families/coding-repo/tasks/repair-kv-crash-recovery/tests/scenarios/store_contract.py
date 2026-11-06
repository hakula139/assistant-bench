import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tinykv
from tinykv import Store, StoreClosed, StoreError


def pairs(items: list[tuple[bytes, bytes]]) -> list[list[str]]:
    return [[key.hex(), value.hex()] for key, value in items]


def hexed(value: bytes | None) -> str | None:
    return None if value is None else value.hex()


def raises(error: type[Exception], action: Callable[[], object]) -> bool:
    """Tell whether `action` raised `error`."""
    try:
        action()
    except error:
        return True
    return False


def api_keeps_its_behavior(directory: str) -> dict[str, Any]:
    path = Path(directory)
    with Store.open(path) as store:
        store.put(b'\x00binary', b'')
        store.put(b'b', b'2')
        store.put(b'a', b'1')
        deleted = [store.delete(b'missing'), store.delete(b'b')]
        rejected = [
            raises(ValueError, lambda: store.put(b'k' * 65536, b'v')),
            raises(ValueError, lambda: store.put(b'k', b'v' * (16 * 1024 * 1024 + 1))),
            raises(ValueError, lambda: Store.open(path / 'other', segment_bytes=63)),
        ]

    with Store.open(path) as store:
        reopened = {
            'items': pairs(store.items()),
            'prefixed': pairs(store.items(b'a')),
            'binary': hexed(store.get(b'\x00binary')),
            'missing': hexed(store.get(b'b')),
            'length': len(store),
        }

    return {
        'deleted': deleted,
        'rejected': rejected,
        'reopened': reopened,
        'closed': raises(StoreClosed, store.items),
    }


def fill_segments(directory: str) -> None:
    with Store.open(Path(directory), segment_bytes=64) as store:
        for index in range(40):
            store.put(b'counter', str(index).encode() * 8)
            store.put(f'key-{index:02}'.encode(), b'x' * 16)


def replay_segments(directory: str) -> dict[str, Any]:
    with Store.open(Path(directory), segment_bytes=64) as store:
        return {'counter': hexed(store.get(b'counter')), 'length': len(store)}


def reopen_current_format(directory: str) -> dict[str, Any]:
    path = Path(directory)
    with Store.open(path) as store:
        opened = {'version': hexed(store.get(b'version')), 'length': len(store)}
        store.put(b'version', b'13')

    with Store.open(path) as store:
        written = hexed(store.get(b'version'))
        store.compact()

    with Store.open(path) as store:
        compacted = {'version': hexed(store.get(b'version')), 'length': len(store)}

    return {'opened': opened, 'written': written, 'compacted': compacted}


def seal_segments(directory: str) -> None:
    with Store.open(Path(directory), segment_bytes=64) as store:
        for index in range(8):
            store.put(f'key-{index}'.encode(), b'v' * 24)


def compact_one_entry(directory: str) -> None:
    with Store.open(Path(directory)) as store:
        store.put(b'key', b'value')
        store.compact()


def opening_raises(directory: str, error: str, **options: Any) -> bool:
    """Tell whether opening the store raises the `tinykv` error named `error`."""
    return raises(
        getattr(tinykv, error), lambda: Store.open(Path(directory), **options)
    )


def store_kept(directory: str) -> None:
    with Store.open(Path(directory)) as store:
        store.put(b'kept', b'1')


def recover_and_put(directory: str) -> dict[str, Any]:
    """Open the store, put an entry, and reopen it, for the entries after each open."""
    path = Path(directory)
    try:
        store = Store.open(path)
    except StoreError as error:
        return {'error': repr(error)}
    with store:
        recovered = pairs(store.items())
        store.put(b'after', b'3')

    with Store.open(path) as store:
        return {'error': None, 'recovered': recovered, 'reopened': pairs(store.items())}


def crash_after_unsynced_put(directory: str) -> None:
    store = Store.open(Path(directory), sync=False)
    store.put(b'key', b'value')
    os._exit(0)


def items(directory: str) -> list[list[str]]:
    with Store.open(Path(directory)) as store:
        return pairs(store.items())
