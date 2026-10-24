"""The reference ``durations`` module with ``parse_duration`` left unimplemented."""

UNITS = (("d", 86_400_000), ("h", 3_600_000), ("m", 60_000), ("s", 1_000), ("ms", 1))


def parse_duration(text: str) -> int:
    raise NotImplementedError


def format_duration(ms: int) -> str:
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
