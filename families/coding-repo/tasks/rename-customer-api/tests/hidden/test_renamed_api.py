import dataclasses
import unittest
import warnings

from shop import repository
from shop.api import handle_customer_summary, handle_get_customer
from shop.models import Customer
from shop.report import customer_report
from shop.service import greeting


class RenamedApiTest(unittest.TestCase):
    def test_field_renamed(self):
        names = [field.name for field in dataclasses.fields(Customer)]
        self.assertEqual(names, ["id", "display_name", "email"])

    def test_find_customer(self):
        self.assertEqual(repository.find_customer(1), Customer(1, "Ada Lovelace", "ada@example.com"))
        with self.assertRaises(LookupError):
            repository.find_customer(42)

    def test_deprecated_alias(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            customer = repository.get_customer(3)
        self.assertEqual(customer, repository.find_customer(3))
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))

    def test_alias_missing_customer(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with self.assertRaises(LookupError):
                repository.get_customer(42)

    def test_callers(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            self.assertEqual(greeting(2), "Welcome back, Grace!")
            self.assertEqual(customer_report([2]), "#2 Grace Hopper <grace@example.com>: 0 orders, $0.00")
            self.assertEqual(handle_get_customer(1), {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"})
            self.assertEqual(handle_customer_summary(3), {"name": "Alan Turing", "lifetime_value_cents": 12000})
