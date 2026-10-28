"""Byte range requests as RFC 9110 section 14 defines them."""

import re
import sys
from dataclasses import dataclass


_RANGE_SPEC = re.compile(r'([0-9]*)-([0-9]*)')
_MAX_DIGITS = len(str(sys.maxsize)) - 1

type RangeSpec = tuple[int | None, int | None]
"""A range's first and last positions, where a missing first gives a suffix."""


@dataclass(frozen=True)
class ByteRange:
    start: int
    stop: int
    """One past the last byte."""

    @property
    def length(self) -> int:
        return self.stop - self.start

    def content_range(self, total: int) -> str:
        return f'bytes {self.start}-{self.stop - 1}/{total}'


def parse_range(field: str) -> list[RangeSpec] | None:
    """Parse a `Range` field, or return `None` when it must be ignored."""
    unit, equals, specs = field.partition('=')
    if not equals or unit.lower() != 'bytes':
        return None

    parsed: list[RangeSpec] = []
    for item in specs.split(','):
        item = item.strip(' \t')
        if not item:
            continue

        match = _RANGE_SPEC.fullmatch(item)
        if match is None:
            return None
        first, last = (group.lstrip('0') or group[:1] for group in match.groups())
        if not first and not last:
            return None
        if first and last and (len(first), first) > (len(last), last):
            return None
        parsed.append((_position(first), _position(last)))

    return parsed or None


def _position(digits: str) -> int | None:
    """Read a position, saturating one too long for any file to reach.

    Saturating avoids converting thousands of digits, which Python refuses.
    """
    if not digits:
        return None
    return int(digits) if len(digits) <= _MAX_DIGITS else sys.maxsize


def satisfiable(specs: list[RangeSpec], length: int) -> list[ByteRange]:
    """Return the ranges that select bytes of a `length`-byte representation.

    Ranges keep their request order, except that overlapping and adjacent ones
    merge where the first of them stood, so no byte is sent twice.
    """
    merged: list[ByteRange] = []
    for first, last in specs:
        if first is None:
            assert last is not None
            if not last:
                continue
            selected = ByteRange(max(length - last, 0), length)
        elif first < length:
            stop = length if last is None else min(last + 1, length)
            selected = ByteRange(first, stop)
        else:
            continue

        touching = [
            index
            for index, kept in enumerate(merged)
            if selected.start <= kept.stop and kept.start <= selected.stop
        ]
        for index in touching:
            kept = merged[index]
            selected = ByteRange(
                min(kept.start, selected.start), max(kept.stop, selected.stop)
            )
        for index in reversed(touching[1:]):
            del merged[index]
        if touching:
            merged[touching[0]] = selected
        else:
            merged.append(selected)
    return merged


def if_range_allows(field: str | None, etag: str) -> bool:
    """Whether an `If-Range` field lets the request's ranges apply.

    Only the representation's strong entity tag does. A date never does, since
    a file can change twice within the second its `Last-Modified` names.
    """
    return field is None or field.strip() == etag
