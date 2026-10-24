import unittest

from textstats.core import tokenize, unique_words, word_count


class TokenizeTest(unittest.TestCase):
    def test_lowercases_and_splits(self):
        self.assertEqual(tokenize("Hello, World! 42 times"), ["hello", "world", "42", "times"])

    def test_apostrophes(self):
        self.assertEqual(tokenize("'Tis don't ''"), ["tis", "don't"])


class CountTest(unittest.TestCase):
    def test_word_count(self):
        self.assertEqual(word_count("a b  c\nd"), 4)

    def test_unique_words(self):
        self.assertEqual(unique_words("A a b B c"), 3)


if __name__ == "__main__":
    unittest.main()
