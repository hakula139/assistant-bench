"""Money formatting."""


def format_cents(cents: int) -> str:
    """Format an amount in cents as dollars with thousands separators, such as ``-$1,234.05``."""
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rest:02d}"
