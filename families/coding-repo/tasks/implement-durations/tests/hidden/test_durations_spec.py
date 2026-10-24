import random
import unittest

from durations import format_duration, parse_duration


class ParseTest(unittest.TestCase):
    def test_valid(self):
        cases = {
            "1h30m": 5_400_000, "2d 4h": 187_200_000, "1500ms": 1500, "0": 0, "0s": 0, "90m": 5_400_000,
            "1ms": 1, "1m1ms": 60_001, "1d1ms": 86_400_001, "1d 1h 1m 1s 1ms": 90_061_001,
            "  45s  ": 45_000, "10d": 864_000_000, "1m 0s": 60_000, "007s": 7000, "2h0m": 7_200_000, "0 ": 0,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)

    def test_invalid(self):
        cases = ["", "   ", "h", "10", "1h30", "-1h", "+1h", "1.5h", "1H", "1x", "1s1s", "1s1m", "1m1h",
                 "1ms1s", "1h  30m", "1 h", "1h,30m", "1h\t30m", "00", "s1", "1hh", "1sm"]
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class FormatTest(unittest.TestCase):
    def test_values(self):
        cases = {0: "0s", 1: "1ms", 999: "999ms", 1000: "1s", 61_000: "1m1s", 3_600_000: "1h",
                 86_400_000: "1d", 90_061_001: "1d1h1m1s1ms", 5_400_000: "1h30m", 172_800_500: "2d500ms"}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(format_duration(value), expected)

    def test_errors(self):
        with self.assertRaises(ValueError):
            format_duration(-1)
        for bad in (1.5, "10", True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    format_duration(bad)

    def test_round_trip(self):
        rng = random.Random(1017)
        for _ in range(500):
            value = rng.choice([rng.randrange(0, 1000), rng.randrange(0, 10**6), rng.randrange(0, 10**11)])
            self.assertEqual(parse_duration(format_duration(value)), value)
