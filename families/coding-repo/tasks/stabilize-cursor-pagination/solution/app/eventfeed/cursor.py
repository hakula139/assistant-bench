"""Snapshot-bound feed continuation tokens."""

import base64
import binascii
import json
from dataclasses import asdict, dataclass

from .models import CursorError


@dataclass(frozen=True)
class Cursor:
    snapshot: int
    occurred_at: int
    id: str
    tag: str | None
    version: int = 1


def _parse_fields(data: dict[str, object]) -> Cursor:
    version = data.get('version')
    snapshot = data.get('snapshot')
    occurred_at = data.get('occurred_at')
    identifier = data.get('id')
    tag = data.get('tag')
    if (
        set(data) != {'version', 'snapshot', 'occurred_at', 'id', 'tag'}
        or type(version) is not int
        or version != 1
        or not isinstance(snapshot, int)
        or isinstance(snapshot, bool)
        or not 0 <= snapshot < 2**63
        or not isinstance(occurred_at, int)
        or isinstance(occurred_at, bool)
        or not -(2**63) <= occurred_at < 2**63
        or not isinstance(identifier, str)
        or not identifier
        or (tag is not None and not isinstance(tag, str))
    ):
        raise ValueError('Invalid cursor fields.')

    return Cursor(snapshot, occurred_at, identifier, tag)


def encode(value: Cursor) -> str:
    content = json.dumps(asdict(value), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(content).decode()


def decode(value: str) -> Cursor:
    try:
        data: object = json.loads(
            base64.b64decode(value, altchars=b'-_', validate=True),
            object_hook=_parse_fields,
        )
        if not isinstance(data, Cursor):
            raise ValueError('Invalid cursor object.')

        return data
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise CursorError('Invalid cursor.') from exc
