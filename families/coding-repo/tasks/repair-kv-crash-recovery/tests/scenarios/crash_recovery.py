from pathlib import Path
from typing import Any

from tinykv import Store


def entries(store: Store) -> dict[str, str]:
    return {key.hex(): value.hex() for key, value in store.items()}


def recover(directory: str, segment_bytes: int, probe: list[str]) -> dict[str, Any]:
    """Open the store, put the hex `probe` entry, and reopen it.

    Return the entries after each open, or the error that stopped recovery.
    """
    key, value = (bytes.fromhex(part) for part in probe)
    try:
        with Store.open(Path(directory), segment_bytes=segment_bytes) as store:
            recovered = entries(store)
            store.put(key, value)
        with Store.open(Path(directory), segment_bytes=segment_bytes) as store:
            return {'error': None, 'recovered': recovered, 'probed': entries(store)}
    except Exception as error:
        return {'error': repr(error)}
