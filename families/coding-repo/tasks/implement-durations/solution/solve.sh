#!/usr/bin/env bash

set -euo pipefail

cat >/app/durations.py <<'PY'
"""Parse and format human-readable durations such as ``1h30m``."""

import re

UNITS = (("d", 86_400_000), ("h", 3_600_000), ("m", 60_000), ("s", 1_000), ("ms", 1))
_RANKS = {unit: rank for rank, (unit, _) in enumerate(UNITS)}
_PIECE = re.compile(r"([0-9]+)(ms|d|h|m|s)")
_COMPONENT = re.compile(r"(?:[0-9]+(?:ms|d|h|m|s))+")


def parse_duration(text: str) -> int:
    """Return the duration in ``text`` as a whole number of milliseconds.

    ``text`` is one or more components, each a run of ASCII digits followed directly by a unit:
    ``d``, ``h``, ``m``, ``s``, or ``ms``. Units are case-sensitive, appear in that descending
    order, and each appears at most once. Adjacent components may be separated by a single
    space. Leading and trailing whitespace is ignored. A component may exceed the next larger
    unit, so ``90m`` is valid. The bare string ``0`` is also accepted and means zero.

    Raise ``ValueError`` for anything else, including an empty string, a sign, a decimal point,
    an unknown unit, a repeated or out-of-order unit, a number without a unit, and more than one
    space between components.

    >>> parse_duration("1h30m")
    5400000
    >>> parse_duration("2d 4h")
    187200000
    >>> parse_duration("1500ms")
    1500
    """
    stripped = text.strip()
    if stripped == "0":
        return 0
    total, last_rank = 0, -1
    for piece in stripped.split(" "):
        match = _COMPONENT.fullmatch(piece) if piece else None
        if match is None:
            raise ValueError(f"invalid duration: {text!r}")
        for number, unit in _PIECE.findall(piece):
            rank = _RANKS[unit]
            if rank <= last_rank:
                raise ValueError(f"unit {unit!r} is repeated or out of order in {text!r}")
            last_rank = rank
            total += int(number) * UNITS[rank][1]
    return total


def format_duration(ms: int) -> str:
    """Format ``ms`` milliseconds as a canonical duration string.

    Use each unit of ``UNITS`` from the largest down, omit zero components, and join the
    components without spaces, so that ``parse_duration(format_duration(x)) == x`` for every
    ``x >= 0``. Zero formats as ``0s``. Raise ``TypeError`` when ``ms`` is not an ``int``,
    including ``bool``, and ``ValueError`` when it is negative.

    >>> format_duration(5400000)
    '1h30m'
    >>> format_duration(1500)
    '1s500ms'
    >>> format_duration(0)
    '0s'
    """
    if isinstance(ms, bool) or not isinstance(ms, int):
        raise TypeError("ms must be an int")
    if ms < 0:
        raise ValueError("ms must not be negative")
    parts = []
    for unit, size in UNITS:
        count, ms = divmod(ms, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts) or "0s"
PY

cat >/app/tests/test_durations.py <<'PY'
import unittest

from durations import format_duration, parse_duration


class DurationsTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("1h 30m"), 5_400_000)

    def test_parse_invalid(self):
        with self.assertRaises(ValueError):
            parse_duration("1s1m")

    def test_format(self):
        self.assertEqual(format_duration(1500), "1s500ms")
        self.assertEqual(format_duration(0), "0s")
PY
