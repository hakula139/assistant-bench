"""Conditional request evaluation in RFC 9110's precedence order."""

import re
from dataclasses import dataclass

from .dates import parse_http_date
from .http import Headers


_ENTITY_TAG = re.compile(r'(W/)?"[\x21\x23-\x7e\x80-\xff]*"')


@dataclass(frozen=True)
class Validators:
    etag: str
    """The selected representation's strong entity tag."""

    modified: int


def evaluate(method: str, headers: Headers, validators: Validators) -> int | None:
    """Return 304 or 412 when a precondition decides the response, else `None`."""
    if_match = headers.get('If-Match')
    if if_match is not None:
        if not _matches(if_match, validators.etag, weak=False):
            return 412
    else:
        since = _date(headers.get('If-Unmodified-Since'))
        if since is not None and validators.modified > since:
            return 412

    safe = method in {'GET', 'HEAD'}
    if_none_match = headers.get('If-None-Match')
    if if_none_match is not None:
        if _matches(if_none_match, validators.etag, weak=True):
            return 304 if safe else 412
    elif safe:
        since = _date(headers.get('If-Modified-Since'))
        if since is not None and validators.modified <= since:
            return 304

    return None


def _matches(field: str, etag: str, *, weak: bool) -> bool:
    if field.strip() == '*':
        return True

    for match in _ENTITY_TAG.finditer(field):
        tag = match.group()
        if weak and tag.removeprefix('W/') == etag:
            return True
        if not weak and tag == etag:
            return True

    return False


def _date(value: str | None) -> int | None:
    return None if value is None else parse_http_date(value)
