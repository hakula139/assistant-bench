import tempfile
import unittest
from pathlib import Path

from shopstore import customer_json, customer_report, open_store


class TestCustomerReport(unittest.TestCase):
    def test_public_name_and_order_total_are_stable(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            open_store(Path(directory) / 'customers.sqlite') as store,
        ):
            store.create_customer(1, 'Ada')
            store.create_order(1, 1, 500)

            serialized = customer_json(store, 1)
            report = customer_report(store)

            self.assertEqual(serialized, {'id': 1, 'name': 'Ada'})
            self.assertEqual(
                report,
                {
                    'customers': [
                        {'id': 1, 'name': 'Ada', 'orders': 1, 'total_cents': 500},
                    ],
                    'total_cents': 500,
                },
            )


if __name__ == '__main__':
    unittest.main()
