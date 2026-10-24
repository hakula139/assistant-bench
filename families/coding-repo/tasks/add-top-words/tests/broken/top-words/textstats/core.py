"""The reference ``textstats.core`` with a ``top_words`` that finds no words."""

import re
from collections.abc import Iterable

_WORD = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    words = (match.strip("'") for match in _WORD.findall(text.lower()))
    return [word for word in words if word]


def word_count(text: str) -> int:
    return len(tokenize(text))


def unique_words(text: str) -> int:
    return len(set(tokenize(text)))


def top_words(text: str, n: int, stopwords: Iterable[str] = ()) -> list[tuple[str, int]]:
    if n < 1:
        raise ValueError("n must be at least 1")
    return []
