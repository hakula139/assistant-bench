import csv
import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

import sandbox


class ImportFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = sandbox.scratch(writable=False)
        self.data = sandbox.scratch()
        self.db = self.data / 'ledger.db'
        self.csv_path = self.root / 'entries.csv'

    def write_rows(self, rows: list[tuple[str, str, str]]) -> None:
        with self.csv_path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(('external_id', 'amount', 'memo'))
            writer.writerows(rows)

    def import_file(self, db: Path, source_id: str, **options: Any) -> dict[str, Any]:
        return sandbox.observe(
            'imports',
            'import_source',
            db=str(db),
            source=str(self.csv_path),
            source_id=source_id,
            **options,
        )

    def ledger(self, db: Path, source_id: str | None = None) -> dict[str, Any]:
        return sandbox.observe('imports', 'ledger', db=str(db), source_id=source_id)

    def raised(self, outcome: dict[str, Any], kind: str) -> dict[str, Any]:
        if kind not in outcome.get('raised', ()):
            self.fail(f'{kind} not raised: {outcome}')
        return outcome

    def ledgerload(self, *arguments: str, check: bool = False) -> dict[str, Any]:
        return sandbox.observe('imports', 'cli', arguments=list(arguments), check=check)

    def test_reordered_headers_quoted_records_and_exact_totals(self) -> None:
        with self.csv_path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(('memo', 'amount', 'external_id'))
            writer.writerow(('quoted, memo\n下一行', '92233720368547758.07', '大'))
            writer.writerow(('', '92233720368547758.07', 'second'))
            writer.writerow(())
            writer.writerow(('zero', '-0.00', 'zero'))

        result = self.import_file(self.db, 'quoted', batch_size=2)

        self.assertEqual(
            result, {'result': {'processed': 3, 'inserted': 3, 'checkpoint': 3}}
        )
        stored = self.ledger(self.db)
        self.assertCountEqual(
            stored['rows'],
            [
                ['大', 9223372036854775807, 'quoted, memo\n下一行'],
                ['second', 9223372036854775807, ''],
                ['zero', 0, 'zero'],
            ],
        )
        self.assertEqual(
            stored['report'], {'rows': 3, 'total_cents': 18446744073709551614}
        )

    def test_failed_checkpoint_rolls_back_batch_and_retry_skips_committed_records(
        self,
    ) -> None:
        self.write_rows(
            [
                ('a', '1.00', ''),
                ('a', '1.00', ''),
                ('b', '-2.01', 'b'),
                ('c', '0.03', 'c'),
                ('d', '4.00', 'd'),
            ]
        )
        sandbox.observe(
            'imports',
            'execute',
            db=str(self.db),
            statement="""
            CREATE TRIGGER stop_second_batch BEFORE UPDATE OF checkpoint ON sources
            WHEN NEW.checkpoint = 4
            BEGIN
                SELECT RAISE(ABORT, 'checkpoint unavailable');
            END
            """,
        )

        self.raised(self.import_file(self.db, 'upload', batch_size=2), 'sqlite3.Error')

        stored = self.ledger(self.db, 'upload')
        self.assertEqual(stored['rows'], [['a', 100, '']])
        self.assertEqual(stored['checkpoint'], 2)
        self.assertEqual(
            stored['digest'],
            hashlib.sha256(self.csv_path.read_bytes()).hexdigest(),
        )
        sandbox.observe(
            'imports',
            'execute',
            db=str(self.db),
            statement='DROP TRIGGER stop_second_batch',
        )

        result = self.import_file(self.db, 'upload', batch_size=2)

        self.assertEqual(
            result, {'result': {'processed': 3, 'inserted': 3, 'checkpoint': 5}}
        )
        stored = self.ledger(self.db, 'upload')
        self.assertCountEqual(
            stored['rows'],
            [
                ['a', 100, ''],
                ['b', -201, 'b'],
                ['c', 3, 'c'],
                ['d', 400, 'd'],
            ],
        )
        self.assertEqual(stored['checkpoint'], 5)
        self.assertEqual(stored['report'], {'rows': 4, 'total_cents': 302})

        self.assertEqual(
            self.import_file(self.db, 'upload'),
            {'result': {'processed': 0, 'inserted': 0, 'checkpoint': 5}},
        )

    def test_conflict_rolls_back_only_its_batch(self) -> None:
        for amount, memo in [('2.00', 'original'), ('1.00', 'changed')]:
            with self.subTest(amount=amount, memo=memo):
                db = self.data / f'conflict-{amount}-{memo}.db'
                self.write_rows(
                    [
                        ('a', '1.00', 'original'),
                        ('b', '2.00', ''),
                        ('c', '3.00', ''),
                        ('a', amount, memo),
                    ]
                )

                caught = self.raised(
                    self.import_file(db, 'conflict', batch_size=2), 'ConflictError'
                )

                self.assertEqual(caught['record_index'], 4)
                stored = self.ledger(db, 'conflict')
                self.assertCountEqual(
                    stored['rows'], [['a', 100, 'original'], ['b', 200, '']]
                )
                self.assertEqual(stored['checkpoint'], 2)

    def test_source_bytes_are_bound_even_after_first_batch_fails(self) -> None:
        self.write_rows([('a', '1.00', 'first'), ('b', '2.00', '')])
        original_digest = hashlib.sha256(self.csv_path.read_bytes()).hexdigest()
        sandbox.observe(
            'imports',
            'execute',
            db=str(self.db),
            statement="""
            CREATE TRIGGER stop_first_batch BEFORE UPDATE OF checkpoint ON sources
            BEGIN
                SELECT RAISE(ABORT, 'checkpoint unavailable');
            END
            """,
        )

        self.raised(self.import_file(self.db, 'bound', batch_size=2), 'sqlite3.Error')

        stored = self.ledger(self.db, 'bound')
        self.assertEqual(stored['rows'], [])
        self.assertEqual(stored['checkpoint'], 0)
        self.assertEqual(stored['digest'], original_digest)
        sandbox.observe(
            'imports',
            'execute',
            db=str(self.db),
            statement='DROP TRIGGER stop_first_batch',
        )
        self.write_rows([('a', '1.00', 'first'), ('b', '2.50', '')])

        changed = self.raised(
            self.import_file(self.db, 'bound', batch_size=2), 'SourceError'
        )

        self.assertEqual(changed['record_index'], 0)
        stored = self.ledger(self.db, 'bound')
        self.assertEqual(stored['rows'], [])
        self.assertEqual(stored['checkpoint'], 0)
        self.assertEqual(stored['digest'], original_digest)

    def test_invalid_amounts_and_ids_report_logical_record_index(self) -> None:
        invalid_rows = [
            ('', '1.00', ''),
            *[
                ('bad', amount, '')
                for amount in (
                    '1',
                    '1.0',
                    '1.000',
                    '+1.00',
                    '１.00',
                    '1e2',
                    '.01',
                    '-.01',
                    ' 1.00',
                    'NaN',
                    '92233720368547758.08',
                    '-92233720368547758.09',
                )
            ],
        ]
        for index, row in enumerate(invalid_rows):
            with self.subTest(row=row):
                db = self.data / f'invalid-{index}.db'
                self.write_rows(
                    [('first', '-92233720368547758.08', 'quoted\nrecord'), row]
                )

                caught = self.raised(
                    self.import_file(db, 'invalid', batch_size=2), 'SourceError'
                )

                self.assertEqual(caught['record_index'], 2)
                stored = self.ledger(db, 'invalid')
                self.assertEqual(stored['rows'], [])
                self.assertEqual(stored['checkpoint'], 0)

    def test_bad_headers_encoding_and_batch_sizes_fail_without_rows(self) -> None:
        documents = (
            b'external_id,amount\na,1.00\n',
            b'external_id,amount,memo,extra\na,1.00,,x\n',
            b'external_id,amount,amount\na,1.00,1.00\n',
            b'external_id,amount,memo\na,1.00,\xff\n',
        )
        for index, content in enumerate(documents):
            with self.subTest(content=content):
                db = self.data / f'file-{index}.db'
                self.csv_path.write_bytes(content)

                caught = self.raised(
                    self.import_file(db, 'invalid-file'), 'SourceError'
                )

                self.assertEqual(caught['record_index'], 0)
                self.assertEqual(self.ledger(db)['rows'], [])

        self.write_rows([('valid', '1.00', '')])
        for batch_size in (0, -1):
            with self.subTest(batch_size=batch_size):
                absent = self.data / f'not-opened-{batch_size}.db'

                self.raised(
                    self.import_file(absent, 'valid', batch_size=batch_size),
                    'ValueError',
                )

                self.assertFalse(absent.exists())

    def test_cli_import_report_replay_and_failure(self) -> None:
        self.write_rows([('one', '0.10', ''), ('two', '-0.03', 'quoted, memo')])
        arguments = [
            'import',
            '--db',
            str(self.db),
            '--file',
            str(self.csv_path),
            '--source-id',
            'cli',
            '--batch-size',
            '1',
        ]

        imported = self.ledgerload(*arguments, check=True)
        report = self.ledgerload('report', '--db', str(self.db), check=True)
        replay = self.ledgerload(*arguments, check=True)

        self.assertEqual(
            json.loads(imported['stdout']),
            {'processed': 2, 'inserted': 2, 'checkpoint': 2},
        )
        self.assertEqual(json.loads(report['stdout']), {'rows': 2, 'total_cents': 7})
        self.assertEqual(
            json.loads(replay['stdout']),
            {'processed': 0, 'inserted': 0, 'checkpoint': 2},
        )

        self.write_rows([('one', '0.11', '')])
        rejected = self.ledgerload(*arguments)

        self.assertNotEqual(rejected['returncode'], 0)
        self.assertTrue(rejected['stderr'].strip())
