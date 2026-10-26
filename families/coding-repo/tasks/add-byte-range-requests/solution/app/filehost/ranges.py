"""Byte range requests as RFC 9110 section 14 defines them."""

import re
from dataclasses import dataclass

from .dates import parse_http_date


_RANGE_SPEC = re.compile(r'([0-9]*)-([0-9]*)')

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
        first, last = (int(group) if group else None for group in match.groups())
        if first is None and last is None:
            return None
        if first is not None and last is not None and first > last:
            return None
        parsed.append((first, last))

    return parsed or None


def satisfiable(specs: list[RangeSpec], length: int) -> list[ByteRange]:
    """Return the ranges that select bytes of a `length`-byte representation.

    Overlapping and adjacent ranges merge, in ascending order, so a request
    cannot make the server send the same bytes more than once.
    """
    ranges: list[ByteRange] = []
    for first, last in specs:
        if first is None:
            assert last is not None
            if last and length:
                ranges.append(ByteRange(max(length - last, 0), length))
        elif first < length:
            stop = length if last is None else min(last + 1, length)
            ranges.append(ByteRange(first, stop))

    merged: list[ByteRange] = []
    for byte_range in sorted(ranges, key=lambda candidate: candidate.start):
        if merged and byte_range.start <= merged[-1].stop:
            previous = merged.pop()
            byte_range = ByteRange(previous.start, max(previous.stop, byte_range.stop))
        merged.append(byte_range)
    return merged


def if_range_allows(field: str | None, etag: str, modified: int) -> bool:
    """Whether an `If-Range` field lets the request's ranges apply.

    An entity tag must match strongly, so a weak tag never does, and a date must
    equal the representation's modification time.
    """
    if field is None:
        return True

    field = field.strip()
    if field.startswith(('"', 'W/')):
        return field == etag

    date = parse_http_date(field)
    return date is not None and date == modified
