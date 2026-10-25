"""Persistent schema, caller, and rollback contracts for customer stores."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from shopstore import (
    CustomerNotFoundError,
    MigrationError,
    SchemaVersionError,
    customer_json,
    customer_report,
    migrate,
    open_store,
)


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCHEMA = ROOT / 'schema-v1.sql'


def snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    return (
        connection.execute('PRAGMA user_version').fetchone()[0],
        connection.execute(
            'SELECT type, name, tbl_name, sql FROM sqlite_master '
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall(),
        connection.execute('SELECT * FROM customers ORDER BY id').fetchall(),
        connection.execute('SELECT * FROM orders ORDER BY id').fetchall(),
    )


class TestCustomerMigration(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / 'customers.sqlite'

    def legacy(self, names: list[tuple[int, str | None]]) -> None:
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.executescript(LEGACY_SCHEMA.read_text())
            connection.executemany(
                'INSERT INTO customers(id, full_name) VALUES (?, ?)', names
            )
            connection.executemany(
                'INSERT INTO orders(id, customer_id, amount_cents) VALUES (?, ?, ?)',
                [(11, 2, 125), (12, 2, 375), (13, 7, 90)],
            )

    def assert_schema(self, connection: sqlite3.Connection) -> None:
        columns = {
            row[1]: row for row in connection.execute('PRAGMA table_info(customers)')
        }
        self.assertNotIn('full_name', columns)
        self.assertEqual(columns['display_name'][3], 1)
        self.assertEqual(
            [row[2] for row in connection.execute('PRAGMA index_info(customers_name)')],
            ['display_name'],
        )
        self.assertEqual(
            [
                row[2]
                for row in connection.execute('PRAGMA index_info(orders_customer_id)')
            ],
            ['customer_id'],
        )
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_empty_store_uses_v2_and_public_callers_keep_serialized_names(self) -> None:
        with open_store(self.db) as store:
            self.assertEqual(
                store.connection.execute('PRAGMA user_version').fetchone()[0], 2
            )
            customer = store.create_customer(2, '  Zoë 王  ')
            empty = store.create_customer(7, '')
            store.create_order(10, customer.id, 450)

            self.assertEqual(customer.display_name, '  Zoë 王  ')
            self.assertEqual(empty.display_name, '')
            self.assertEqual(customer_json(store, 2), {'id': 2, 'name': '  Zoë 王  '})
            self.assertEqual(
                customer_report(store),
                {
                    'customers': [
                        {
                            'id': 2,
                            'name': '  Zoë 王  ',
                            'orders': 1,
                            'total_cents': 450,
                        },
                        {'id': 7, 'name': '', 'orders': 0, 'total_cents': 0},
                    ],
                    'total_cents': 450,
                },
            )
            self.assert_schema(store.connection)
            with self.assertRaises(CustomerNotFoundError) as missing:
                store.get_customer(999)
            self.assertEqual(missing.exception.customer_id, 999)

    def test_legacy_rows_relationships_and_indexes_survive_reopen_and_new_writes(
        self,
    ) -> None:
        self.legacy([(7, ''), (2, '  Zoë 王  '), (9, 'Åda')])
        expected = {
            'customers': [
                {'id': 2, 'name': '  Zoë 王  ', 'orders': 2, 'total_cents': 500},
                {'id': 7, 'name': '', 'orders': 1, 'total_cents': 90},
                {'id': 9, 'name': 'Åda', 'orders': 0, 'total_cents': 0},
            ],
            'total_cents': 590,
        }

        with open_store(self.db) as store:
            self.assertEqual(
                store.connection.execute('PRAGMA user_version').fetchone()[0], 2
            )
            self.assertEqual([customer.id for customer in store.customers()], [2, 7, 9])
            self.assertEqual(customer_report(store), expected)
            self.assertEqual(customer_json(store, 2), {'id': 2, 'name': '  Zoë 王  '})
            self.assert_schema(store.connection)

        with open_store(self.db) as reopened:
            self.assertEqual(customer_report(reopened), expected)
            with self.assertRaises(sqlite3.IntegrityError):
                reopened.connection.execute('INSERT INTO orders VALUES (99, 999, 1)')
            reopened.connection.rollback()
            newcomer = reopened.create_customer(12, 'New')
            reopened.create_order(14, newcomer.id, 10)

            self.assertEqual(reopened.get_customer(12).display_name, 'New')
            self.assertEqual(customer_report(reopened)['total_cents'], 600)
            self.assert_schema(reopened.connection)

    def test_null_legacy_name_rolls_back_then_same_connection_can_repair_and_migrate(
        self,
    ) -> None:
        self.legacy([(2, 'valid first row'), (7, None)])
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA foreign_keys = ON')
        before = snapshot(connection)

        with self.assertRaises(MigrationError):
            migrate(connection)

        self.assertEqual(snapshot(connection), before)
        self.assertFalse(connection.in_transaction)
        connection.execute("UPDATE customers SET full_name = 'repaired' WHERE id = 7")
        connection.commit()

        migrate(connection)

        self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
        self.assertEqual(
            connection.execute(
                'SELECT id, display_name FROM customers ORDER BY id'
            ).fetchall(),
            [(2, 'valid first row'), (7, 'repaired')],
        )
        self.assert_schema(connection)

    def test_open_store_rejects_null_without_partial_schema_or_data_changes(
        self,
    ) -> None:
        self.legacy([(2, 'valid'), (7, None)])
        with closing(sqlite3.connect(self.db)) as connection:
            before = snapshot(connection)

        with self.assertRaises(MigrationError):
            open_store(self.db)

        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(snapshot(connection), before)
            self.assertEqual(
                connection.execute('PRAGMA foreign_key_check').fetchall(), []
            )

    def test_unknown_version_is_rejected_without_writes(self) -> None:
        self.legacy([(2, 'Ada'), (7, 'Grace')])
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute('PRAGMA user_version = 99')
            before = snapshot(connection)

        with self.assertRaises(SchemaVersionError) as error:
            open_store(self.db)

        self.assertEqual(error.exception.version, 99)
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute('PRAGMA foreign_keys = ON')

            with self.assertRaises(SchemaVersionError) as migration_error:
                migrate(connection)

            self.assertEqual(migration_error.exception.version, 99)
            self.assertEqual(snapshot(connection), before)
            self.assertFalse(connection.in_transaction)

    def test_cli_report_matches_migrated_api_and_remains_stable_on_reopen(self) -> None:
        self.legacy([(2, '王'), (7, '')])

        first = subprocess.run(
            [sys.executable, '-m', 'shopstore', 'report', '--db', str(self.db)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(
            [sys.executable, '-m', 'shopstore', 'report', '--db', str(self.db)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        with open_store(self.db) as store:
            self.assertEqual(json.loads(first.stdout), customer_report(store))
            self.assertEqual(json.loads(second.stdout), customer_report(store))
            self.assert_schema(store.connection)


if __name__ == '__main__':
    unittest.main()
