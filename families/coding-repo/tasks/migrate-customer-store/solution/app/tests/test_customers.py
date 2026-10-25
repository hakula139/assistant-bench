import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from shopstore import (
    MigrationError,
    customer_json,
    customer_report,
    migrate,
    open_store,
)


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

    def test_failed_conversion_preserves_v1_and_same_connection_can_retry(self) -> None:
        schema = Path(__file__).resolve().parents[1] / 'schema-v1.sql'
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(
                sqlite3.connect(Path(directory) / 'customers.sqlite')
            ) as connection,
        ):
            connection.executescript(schema.read_text())
            connection.executemany(
                'INSERT INTO customers(id, full_name) VALUES (?, ?)',
                [(1, 'Ada'), (2, None)],
            )
            connection.execute('INSERT INTO orders VALUES (1, 1, 500)')
            connection.commit()
            connection.execute('PRAGMA foreign_keys = ON')
            before = list(connection.iterdump())

            with self.assertRaises(MigrationError):
                migrate(connection)

            self.assertEqual(list(connection.iterdump()), before)
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertFalse(connection.in_transaction)
            connection.execute("UPDATE customers SET full_name = 'Grace' WHERE id = 2")
            connection.commit()

            migrate(connection)

            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(
                connection.execute(
                    'SELECT id, display_name FROM customers ORDER BY id'
                ).fetchall(),
                [(1, 'Ada'), (2, 'Grace')],
            )
            self.assertEqual(
                connection.execute('SELECT * FROM orders').fetchall(), [(1, 1, 500)]
            )
            self.assertEqual(
                connection.execute('PRAGMA foreign_key_check').fetchall(), []
            )


if __name__ == '__main__':
    unittest.main()
