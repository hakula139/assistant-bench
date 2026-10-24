"""Parse and format human-readable durations such as ``1h30m``."""

UNITS = (("d", 86_400_000), ("h", 3_600_000), ("m", 60_000), ("s", 1_000), ("ms", 1))


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
    raise NotImplementedError


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
    raise NotImplementedError
