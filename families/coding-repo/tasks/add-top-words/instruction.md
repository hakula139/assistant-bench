Add a `top_words` function to the `textstats` package in `/app` and expose it on the command line.

- `top_words(text: str, n: int, stopwords: Iterable[str] = ()) -> list[tuple[str, int]]` in `textstats/core.py` returns the `n` most frequent words of `text` as `(word, count)` pairs, splitting words by the same rules as `tokenize`. Order the pairs by count, highest first, and break ties alphabetically. Exclude stopwords, matching them case-insensitively. If fewer than `n` distinct words remain, return all of them. Raise `ValueError` when `n` is less than 1.
- Export `top_words` from the `textstats` package.
- Add a `--top N` option to the CLI. With it, the CLI prints one `word<TAB>count` line per pair, in order, instead of the word count. Add a `--stopwords FILE` option naming a file with one stopword per line, which applies only together with `--top`.
- Add unit tests for the function and for the new CLI options to the existing test modules.

Keep the existing behavior unchanged, and make sure `python3 -m unittest` passes.
