import contextlib
import io
import os
import tempfile
import unittest

import textstats
from textstats.cli import main
from textstats.core import top_words


class TopWordsTest(unittest.TestCase):
    def test_most_frequent_first(self):
        self.assertEqual(top_words("b a b c a b", 2), [("b", 3), ("a", 2)])

    def test_ties_alphabetical(self):
        self.assertEqual(top_words("z y x y z", 3), [("y", 2), ("z", 2), ("x", 1)])

    def test_tokenize_rules(self):
        self.assertEqual(top_words("Don't DON'T 'don't' stop", 1), [("don't", 3)])

    def test_stopwords_case_insensitive(self):
        self.assertEqual(top_words("The cat the hat", 5, stopwords=["THE"]), [("cat", 1), ("hat", 1)])

    def test_stopwords_any_iterable(self):
        self.assertEqual(top_words("a b a", 5, stopwords=iter(["A"])), [("b", 1)])

    def test_fewer_than_n(self):
        self.assertEqual(top_words("one two", 10), [("one", 1), ("two", 1)])

    def test_empty_text(self):
        self.assertEqual(top_words("", 3), [])

    def test_invalid_n(self):
        with self.assertRaises(ValueError):
            top_words("a", 0)
        with self.assertRaises(ValueError):
            top_words("a", -2)

    def test_exported(self):
        self.assertIs(textstats.top_words, top_words)


def run_cli(*args: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = main(list(args))
    assert status in (0, None)
    return out.getvalue()


class TopCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.text = os.path.join(self.dir, "text.txt")
        self.stop = os.path.join(self.dir, "stop.txt")
        with open(self.text, "w", encoding="utf-8") as f:
            f.write("The cat and the hat and THE bat\n")
        with open(self.stop, "w", encoding="utf-8") as f:
            f.write("the\nAND\n")

    def test_top(self):
        self.assertEqual(run_cli("--top", "2", self.text), "the\t3\nand\t2\n")

    def test_top_with_stopwords(self):
        self.assertEqual(run_cli("--top", "2", "--stopwords", self.stop, self.text), "bat\t1\ncat\t1\n")

    def test_default_unchanged(self):
        self.assertEqual(run_cli(self.text), "8\n")
        self.assertEqual(run_cli("--unique", self.text), "5\n")
