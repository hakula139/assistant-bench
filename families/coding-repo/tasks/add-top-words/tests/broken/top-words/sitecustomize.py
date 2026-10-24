"""Make `textstats.core.top_words` find no words, while it still rejects `n < 1`."""

from collections.abc import Iterable

from verify_lib import after_import, replace_function


def top_words(
    text: str, n: int, stopwords: Iterable[str] = ()
) -> list[tuple[str, int]]:
    if n < 1:
        raise ValueError('n must be at least 1')
    return []


after_import(
    'textstats.core', lambda module: replace_function(module, 'top_words', top_words)
)
