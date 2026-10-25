"""Candidate that applies keyset ordering without a snapshot ceiling."""

from pathlib import Path

from eventfeed.cursor import Cursor, decode, encode
from eventfeed.models import CursorError, Event, Page
from eventfeed.store import Store


def page(
    db: Path, *, limit: int = 20, tag: str | None = None, cursor: str | None = None
) -> Page:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Limit must be between 1 and 100.')

    previous = decode(cursor) if cursor is not None else None
    if previous is not None and previous.tag != tag:
        raise CursorError('Cursor belongs to another tag filter.')

    with Store(db) as store:
        snapshot = store.highwater() if previous is None else previous.snapshot
        statement = (
            'SELECT id, occurred_at, tag FROM events WHERE (? IS NULL OR tag = ?)'
        )
        parameters: tuple[object, ...] = (tag, tag)
        if previous is not None:
            statement += (
                ' AND (occurred_at > ? OR (occurred_at = ? AND id COLLATE BINARY > ?))'
            )
            parameters += (previous.occurred_at, previous.occurred_at, previous.id)

        statement += ' ORDER BY occurred_at, id COLLATE BINARY LIMIT ?'
        parameters += (limit + 1,)
        rows = store.connection.execute(statement, parameters).fetchall()

    items = [Event(str(row[0]), int(row[1]), str(row[2])) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode(Cursor(snapshot, last.occurred_at, last.id, tag))

    return Page(items, next_cursor)
