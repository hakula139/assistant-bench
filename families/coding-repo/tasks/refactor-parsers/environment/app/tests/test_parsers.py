import unittest
from decimal import Decimal

from records import Order, ParseError, Product, User, parse_order, parse_product, parse_user


class ParsersTest(unittest.TestCase):
    def test_user(self):
        self.assertEqual(parse_user("7|ada@example.com|yes\n"), User(7, "ada@example.com", True))

    def test_order(self):
        self.assertEqual(parse_order(" 3 | 7 | 19.90 "), Order(3, 7, Decimal("19.90")))

    def test_product(self):
        self.assertEqual(parse_product("AB-1|Widget|2.50|12"), Product("AB-1", "Widget", Decimal("2.50"), 12))

    def test_field_count(self):
        with self.assertRaisesRegex(ParseError, "expected 4 fields, got 3"):
            parse_product("AB-1|Widget|2.50")

    def test_bad_integer(self):
        with self.assertRaises(ParseError) as caught:
            parse_order("x|7|1.00")
        self.assertEqual(caught.exception.reason, "id is not an integer: 'x'")


if __name__ == "__main__":
    unittest.main()
