#!/usr/bin/env bash

set -euo pipefail

cd /app
cat >>textstats/core.py <<'PY'


def top_words(text: str, n: int, stopwords: Iterable[str] = ()) -> list[tuple[str, int]]:
    """Return the ``n`` most frequent words as ``(word, count)``, ties broken alphabetically."""
    if n < 1:
        raise ValueError("n must be at least 1")
    skip = {word.lower() for word in stopwords}
    counts = Counter(word for word in tokenize(text) if word not in skip)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]
PY
python3 - <<'PY'
import pathlib

core = pathlib.Path("textstats/core.py")
core.write_text(core.read_text().replace(
    "import re\n", "import re\nfrom collections import Counter\nfrom collections.abc import Iterable\n", 1))

init = pathlib.Path("textstats/__init__.py")
init.write_text(init.read_text()
    .replace("import tokenize, unique_words, word_count", "import tokenize, top_words, unique_words, word_count")
    .replace('["tokenize", "unique_words", "word_count"]', '["tokenize", "top_words", "unique_words", "word_count"]'))

cli = pathlib.Path("textstats/cli.py")
text = cli.read_text()
text = text.replace("import unique_words, word_count", "import top_words, unique_words, word_count")
text = text.replace(
    '    return parser\n',
    '    parser.add_argument("--top", type=int, metavar="N", help="print the N most frequent words")\n'
    '    parser.add_argument("--stopwords", metavar="FILE", help="words to skip with --top, one per line")\n'
    '    return parser\n')
text = text.replace(
    '    print(unique_words(text) if args.unique else word_count(text))\n',
    '    if args.top is not None:\n'
    '        stopwords: list[str] = []\n'
    '        if args.stopwords:\n'
    '            stopwords = [line.strip() for line in _read(args.stopwords).splitlines() if line.strip()]\n'
    '        for word, count in top_words(text, args.top, stopwords):\n'
    '            print(f"{word}\\t{count}")\n'
    '        return 0\n'
    '    print(unique_words(text) if args.unique else word_count(text))\n')
cli.write_text(text)
PY
python3 - <<'PY'
import pathlib

main_guard = '\n\nif __name__ == "__main__":'

core = pathlib.Path("tests/test_core.py")
text = core.read_text().replace(
    "import tokenize, unique_words, word_count", "import tokenize, top_words, unique_words, word_count")
core.write_text(text.replace(main_guard, '''

class TopWordsTest(unittest.TestCase):
    def test_most_frequent_first(self):
        self.assertEqual(top_words("b a b", 2), [("b", 2), ("a", 1)])

    def test_invalid_n(self):
        with self.assertRaises(ValueError):
            top_words("a", 0)
''' + main_guard))

cli = pathlib.Path("tests/test_cli.py")
cli.write_text(cli.read_text().replace(main_guard, '''
    def test_top(self):
        self.assertEqual(run_cli("--top", "2", self.path), "the\\t2\\nand\\t1\\n")

    def test_top_with_stopwords(self):
        handle, stopwords = tempfile.mkstemp(suffix=".txt")
        self.addCleanup(os.unlink, stopwords)
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write("THE\\n")
        self.assertEqual(run_cli("--top", "2", "--stopwords", stopwords, self.path), "and\\t1\\ncat\\t1\\n")
''' + main_guard))
PY
