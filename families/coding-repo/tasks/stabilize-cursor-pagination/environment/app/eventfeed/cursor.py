"""Opaque feed continuation tokens."""

import base64
import binascii
import json
from dataclasses import dataclass

from .models import CursorError


@dataclass(frozen=True)
class _Offset:
    value: int


def _parse_fields(data: dict[str, object]) -> _Offset:
    version = data.get('version')
    offset = data.get('offset')
    if (
        type(version) is not int
        or version != 1
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
    ):
        raise ValueError('Invalid cursor fields.')

    return _Offset(offset)


def encode(offset: int) -> str:
    content = json.dumps({'version': 1, 'offset': offset}).encode()
    return base64.urlsafe_b64encode(content).decode()


def decode(value: str) -> int:
    try:
        data: object = json.loads(
            base64.b64decode(value, altchars=b'-_', validate=True),
            object_hook=_parse_fields,
        )
        if not isinstance(data, _Offset):
            raise ValueError('Invalid cursor object.')

        return data.value
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise CursorError('Invalid cursor.') from exc
