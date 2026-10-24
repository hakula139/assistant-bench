"""The reference ``textstats.cli`` with a ``--stopwords`` option that skips nothing."""

import argparse
import sys
from collections.abc import Sequence

from textstats.core import top_words, unique_words, word_count


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="textstats", description="Word statistics for a text file.")
    parser.add_argument("path", help="the file to read, or - for standard input")
    parser.add_argument("--unique", action="store_true", help="count distinct words instead")
    parser.add_argument("--top", type=int, metavar="N", help="print the N most frequent words")
    parser.add_argument("--stopwords", metavar="FILE", help="words to skip with --top, one per line")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = _read(args.path)
    if args.top is not None:
        for word, count in top_words(text, args.top):
            print(f"{word}\t{count}")
        return 0
    print(unique_words(text) if args.unique else word_count(text))
    return 0
