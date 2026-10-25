# Event feed

The append-only SQLite feed currently paginates by offset. Inserts between pages can shift those offsets and duplicate or omit events.

## Python API

`eventfeed` exports `Event`, `Page`, `CursorError`, `Store` and `page`.

`Event` is a frozen dataclass with `id: str`, `occurred_at: int` and `tag: str`. IDs are unique and nonempty. Times are SQLite integers. `Page` has `items: list[Event]` and `next_cursor: str | None`.

`Store(path: Path)` owns a public `connection: sqlite3.Connection` and supports a closing context manager. `append(event: Event) -> None` commits one new event and rejects existing IDs without changing them. `highwater() -> int` returns the greatest ingestion sequence, or zero for an empty store. No update or delete API is required.

`page(db: Path, *, limit: int = 20, tag: str | None = None, cursor: str | None = None) -> Page` returns events ordered by ascending `(occurred_at, id)`, using SQLite binary text ordering for IDs. Limits are integers from 1 through 100, and may change between pages. A tag of `None` means all tags, while an empty string matches only that tag.

The first page captures the maximum ingestion sequence. Continuation pages use that same ceiling and begin strictly after the previous ordering key. All later inserts are excluded, including backdated events. Existing events are immutable.

A cursor binds its snapshot and tag filter. Changing the filter or supplying malformed or unsupported data raises `CursorError`, a `ValueError` subclass. Cursor encoding is private and callers must pass returned values unchanged. Authentication is not required. An empty or final page has `next_cursor=None`, including a final page exactly as long as the requested limit.

## CLI

`python -m eventfeed append --db feed.db --id event-1 --occurred-at 100 --tag news` appends an event and emits its three fields as JSON. `python -m eventfeed page --db feed.db --limit 20 --tag news --cursor TOKEN` emits `{"items": [...], "next_cursor": ...}`. Omit `--cursor` for the first page. Failures exit nonzero and explain the failure on stderr.

## Public schema

`schema.sql` defines `events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE COLLATE BINARY, occurred_at INTEGER NOT NULL, tag TEXT NOT NULL)` and an ordering index. The immutable sequence measures ingestion, independently of event time.
