"""The reference ``durations`` module with ``format_duration`` left unimplemented."""

import re

UNITS = (("d", 86_400_000), ("h", 3_600_000), ("m", 60_000), ("s", 1_000), ("ms", 1))
_RANKS = {unit: rank for rank, (unit, _) in enumerate(UNITS)}
_PIECE = re.compile(r"([0-9]+)(ms|d|h|m|s)")
_COMPONENT = re.compile(r"(?:[0-9]+(?:ms|d|h|m|s))+")


def parse_duration(text: str) -> int:
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
    raise NotImplementedError
