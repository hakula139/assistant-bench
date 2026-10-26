"""HTTP dates in whole seconds since the epoch."""

from datetime import UTC
from email.utils import formatdate, parsedate_to_datetime


def format_http_date(timestamp: float) -> str:
    """Format a timestamp as an IMF-fixdate, such as `Sun, 06 Nov 1994 08:49:37 GMT`."""
    return formatdate(timestamp, usegmt=True)


def parse_http_date(value: str) -> int | None:
    """Parse any of HTTP's three date formats, or return `None` when invalid."""
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())
