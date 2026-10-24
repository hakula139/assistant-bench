import unittest

from durations import UNITS


class UnitsTest(unittest.TestCase):
    def test_descending(self):
        sizes = [size for _, size in UNITS]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


if __name__ == "__main__":
    unittest.main()
