import dataclasses
import subprocess
import sys
from pathlib import Path
from typing import Any

from eventfeed import CursorError, Event, Store, page as read_page


def append(path: str, events: list[dict[str, Any]]) -> None:
    with Store(Path(path)) as store:
        for event in events:
            store.append(Event(**event))


def page(
    path: str, limit: int, tag: str | None = None, cursor: str | None = None
) -> dict[str, Any]:
    """Read one page, naming the error class instead when the read raises one.

    `CursorError` takes precedence over `ValueError`, which it subclasses.
    """
    try:
        result = read_page(Path(path), limit=limit, tag=tag, cursor=cursor)
    except CursorError:
        return {'error': 'CursorError'}
    except ValueError:
        return {'error': 'ValueError'}
    return {
        'error': None,
        'items': [dataclasses.asdict(event) for event in result.items],
        'next_cursor': result.next_cursor,
    }


def append_after_duplicate(
    path: str, duplicate: dict[str, Any], replacement: dict[str, Any]
) -> dict[str, Any]:
    """Append `duplicate`, then `replacement` once the store rejects it."""
    with Store(Path(path)) as store:
        ceiling = store.highwater()
        try:
            store.append(Event(**duplicate))
        except Exception:
            store.append(Event(**replacement))
            rejected = True
        else:
            rejected = False
        return {
            'rejected': rejected,
            'ceiling': ceiling,
            'highwater': store.highwater(),
        }


def cli(args: list[str]) -> str:
    """Run the `eventfeed` command and return its stdout."""
    return subprocess.run(
        [sys.executable, '-m', 'eventfeed', *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
