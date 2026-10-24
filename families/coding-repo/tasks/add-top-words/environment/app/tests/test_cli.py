import contextlib
import io
import os
import tempfile
import unittest

from textstats.cli import main


def run_cli(*args: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = main(list(args))
    assert status == 0
    return out.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write("the cat and the hat\n")

    def tearDown(self):
        os.unlink(self.path)

    def test_word_count(self):
        self.assertEqual(run_cli(self.path), "5\n")

    def test_unique(self):
        self.assertEqual(run_cli("--unique", self.path), "4\n")


if __name__ == "__main__":
    unittest.main()
