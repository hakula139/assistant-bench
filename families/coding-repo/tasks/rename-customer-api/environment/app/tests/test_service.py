import unittest

from shop.repository import get_customer
from shop.service import greeting, lifetime_value_cents


class ServiceTest(unittest.TestCase):
    def test_greeting(self):
        self.assertEqual(greeting(1), "Welcome back, Ada!")

    def test_lifetime_value(self):
        self.assertEqual(lifetime_value_cents(1), 2499)
        self.assertEqual(lifetime_value_cents(2), 0)

    def test_name(self):
        self.assertEqual(get_customer(3).name, "Alan Turing")


if __name__ == "__main__":
    unittest.main()
