import unittest

from ledger.money import format_cents


class FormatCentsTest(unittest.TestCase):
    def test_thousands(self):
        self.assertEqual(format_cents(123456), "$1,234.56")

    def test_negative(self):
        self.assertEqual(format_cents(-5), "-$0.05")


if __name__ == "__main__":
    unittest.main()
