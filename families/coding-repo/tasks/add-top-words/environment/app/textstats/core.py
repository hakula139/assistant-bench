"""Tokenizing and counting words."""

import re

_WORD = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    """Split ``text`` into lowercase words.

    A word is a run of ASCII letters, digits, and apostrophes, with leading and trailing
    apostrophes removed. Runs made only of apostrophes are dropped.
    """
    words = (match.strip("'") for match in _WORD.findall(text.lower()))
    return [word for word in words if word]


def word_count(text: str) -> int:
    """Return the number of words in ``text``."""
    return len(tokenize(text))


def unique_words(text: str) -> int:
    """Return the number of distinct words in ``text``."""
    return len(set(tokenize(text)))
