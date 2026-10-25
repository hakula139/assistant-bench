"""Ordered feed pages through the event store."""

from pathlib import Path

from .cursor import decode, encode
from .models import Event, Page
from .store import Store


def page(
    db: Path, *, limit: int = 20, tag: str | None = None, cursor: str | None = None
) -> Page:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Limit must be between 1 and 100.')

    offset = decode(cursor) if cursor is not None else 0
    with Store(db) as store:
        rows = store.connection.execute(
            'SELECT id, occurred_at, tag FROM events '
            'WHERE (? IS NULL OR tag = ?) '
            'ORDER BY occurred_at, id COLLATE BINARY LIMIT ? OFFSET ?',
            (tag, tag, limit + 1, offset),
        ).fetchall()

    items = [Event(str(row[0]), int(row[1]), str(row[2])) for row in rows[:limit]]
    next_cursor = encode(offset + limit) if len(rows) > limit else None
    return Page(items, next_cursor)
