"""Comparable text lengths for output records and descriptive diagnostics."""

import re

from zhon import hanzi


# A Han character, or a run up to whitespace or a Han character.
_UNIT = re.compile(f'[{hanzi.characters}]|[^\\s{hanzi.characters}]+')


def text_length(text: str) -> int:
    """Count each Han character and each other whitespace-separated run as one."""
    return len(_UNIT.findall(text))
