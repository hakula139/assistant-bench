import unittest

from shop.api import handle_customer_summary, handle_get_customer
from shop.report import customer_report


class ReportTest(unittest.TestCase):
    def test_report(self):
        self.assertEqual(
            customer_report([3, 1]),
            "#1 Ada Lovelace <ada@example.com>: 2 orders, $24.99\n"
            "#3 Alan Turing <alan@example.com>: 1 orders, $120.00",
        )


class ApiTest(unittest.TestCase):
    def test_get_customer(self):
        self.assertEqual(handle_get_customer(2), {"id": 2, "name": "Grace Hopper", "email": "grace@example.com"})

    def test_summary(self):
        self.assertEqual(handle_customer_summary(1), {"name": "Ada Lovelace", "lifetime_value_cents": 2499})


if __name__ == "__main__":
    unittest.main()
