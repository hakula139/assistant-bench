import unittest

from shop.models import Customer
from shop.repository import get_customer, orders_for


class RepositoryTest(unittest.TestCase):
    def test_get_customer(self):
        self.assertEqual(get_customer(2), Customer(id=2, name="Grace Hopper", email="grace@example.com"))

    def test_missing(self):
        with self.assertRaises(LookupError):
            get_customer(99)

    def test_orders(self):
        self.assertEqual([order.id for order in orders_for(1)], [10, 11])


if __name__ == "__main__":
    unittest.main()
