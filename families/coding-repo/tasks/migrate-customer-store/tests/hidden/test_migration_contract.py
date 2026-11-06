"""Persistent schema, caller, and rollback contracts for customer stores."""

import json
import unittest
from typing import Any

import sandbox


LEGACY_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    full_name TEXT
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL
);
CREATE INDEX customers_name ON customers(full_name);
CREATE INDEX orders_customer_id ON orders(customer_id);
PRAGMA user_version = 1;
"""
"""The complete supported legacy schema, as the workspace's `schema-v1.sql`."""


class TestCustomerMigration(unittest.TestCase):
    def setUp(self) -> None:
        self.db = str(sandbox.scratch() / 'customers.sqlite')

    def observe(self, function: str, **args: Any) -> Any:
        return sandbox.observe('migration', function, path=self.db, **args)

    def legacy(self, names: list[tuple[int, str | None]]) -> None:
        self.observe('legacy', legacy_schema=LEGACY_SCHEMA, names=names)

    def assert_schema(self, schema: dict[str, Any]) -> None:
        columns = schema['columns']
        self.assertNotIn('full_name', columns)
        self.assertEqual(columns['display_name'][3], 1)
        self.assertEqual(schema['customers_name'], ['display_name'])
        self.assertEqual(schema['orders_customer_id'], ['customer_id'])
        self.assertEqual(schema['foreign_key_check'], [])

    def test_empty_store_uses_v2_and_public_callers_keep_serialized_names(self) -> None:
        seen = self.observe('empty_store')

        self.assertEqual(seen['version'], 2)
        self.assertEqual(seen['customer'], '  Zoë 王  ')
        self.assertEqual(seen['empty'], '')
        self.assertEqual(seen['json'], {'id': 2, 'name': '  Zoë 王  '})
        self.assertEqual(
            seen['report'],
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
        self.assert_schema(seen['schema'])
        if seen['missing'] is None:
            self.fail('get_customer(999) raised no CustomerNotFoundError')
        self.assertEqual(seen['missing']['customer_id'], 999)

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

        opened = self.observe('open_legacy')

        self.assertEqual(opened['version'], 2)
        self.assertEqual(opened['ids'], [2, 7, 9])
        self.assertEqual(opened['report'], expected)
        self.assertEqual(opened['json'], {'id': 2, 'name': '  Zoë 王  '})
        self.assert_schema(opened['schema'])

        reopened = self.observe('reopen_and_write')

        self.assertEqual(reopened['report'], expected)
        self.assertTrue(
            reopened['orphan_rejected'], 'an orphan order raised no IntegrityError'
        )
        self.assertEqual(reopened['newcomer'], 'New')
        self.assertEqual(reopened['total_cents'], 600)
        self.assert_schema(reopened['schema'])

    def test_null_legacy_name_rolls_back_then_same_connection_can_repair_and_migrate(
        self,
    ) -> None:
        self.legacy([(2, 'valid first row'), (7, None)])

        seen = self.observe('repair_after_rollback')

        self.assertTrue(seen['rejected'], 'migrate raised no MigrationError')
        self.assertEqual(seen['after'], seen['before'])
        self.assertFalse(seen['in_transaction'])
        self.assertEqual(seen['version'], 2)
        self.assertEqual(seen['rows'], [[2, 'valid first row'], [7, 'repaired']])
        self.assert_schema(seen['schema'])

    def test_open_store_rejects_null_without_partial_schema_or_data_changes(
        self,
    ) -> None:
        self.legacy([(2, 'valid'), (7, None)])
        before = self.observe('stored')['snapshot']

        rejection = self.observe('open_store_rejection', error='MigrationError')

        self.assertIsNotNone(rejection, 'open_store raised no MigrationError')
        after = self.observe('stored')
        self.assertEqual(after['snapshot'], before)
        self.assertEqual(after['foreign_key_check'], [])

    def test_unknown_version_is_rejected_without_writes(self) -> None:
        self.legacy([(2, 'Ada'), (7, 'Grace')])
        self.observe('set_user_version', version=99)
        before = self.observe('stored')['snapshot']

        rejection = self.observe('open_store_rejection', error='SchemaVersionError')

        if rejection is None:
            self.fail('open_store raised no SchemaVersionError')
        self.assertEqual(rejection['version'], 99)
        migration = self.observe('migrate_unknown_version')
        if migration['error'] is None:
            self.fail('migrate raised no SchemaVersionError')
        self.assertEqual(migration['error']['version'], 99)
        self.assertEqual(migration['snapshot'], before)
        self.assertFalse(migration['in_transaction'])

    def test_cli_report_matches_migrated_api_and_remains_stable_on_reopen(self) -> None:
        self.legacy([(2, '王'), (7, '')])

        first = self.observe('cli_report')
        self.assertEqual(first['returncode'], 0, first['stderr'])
        second = self.observe('cli_report')
        self.assertEqual(second['returncode'], 0, second['stderr'])

        stored = self.observe('report')
        self.assertEqual(json.loads(first['stdout']), stored['report'])
        self.assertEqual(json.loads(second['stdout']), stored['report'])
        self.assert_schema(stored['schema'])
