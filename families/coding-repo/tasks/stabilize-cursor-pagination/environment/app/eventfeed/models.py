"""Public event, page and invalid-cursor contracts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    id: str

    occurred_at: int
    """Primary ordering key. Ties use binary ID ordering."""

    tag: str


@dataclass(frozen=True)
class Page:
    items: list[Event]

    next_cursor: str | None
    """Opaque continuation bound to the original snapshot and filter."""


class CursorError(ValueError):
    """The cursor is malformed, unsupported or belongs to another filter."""
