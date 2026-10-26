import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ledgerload import (
    ConflictError,
    ImportResult,
    LedgerRow,
    SourceError,
    Store,
    import_file,
)


class ImportFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / 'ledger.db'
        self.csv_path = self.root / 'entries.csv'

    def write_rows(self, rows: list[tuple[str, str, str]]) -> None:
        with self.csv_path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(('external_id', 'amount', 'memo'))
            writer.writerows(rows)

    def test_reordered_headers_quoted_records_and_exact_totals(self) -> None:
        with self.csv_path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(('memo', 'amount', 'external_id'))
            writer.writerow(('quoted, memo\n下一行', '92233720368547758.07', '大'))
            writer.writerow(('', '92233720368547758.07', 'second'))
            writer.writerow(())
            writer.writerow(('zero', '-0.00', 'zero'))

        result = import_file(self.db, self.csv_path, source_id='quoted', batch_size=2)

        self.assertEqual(result, ImportResult(processed=3, inserted=3, checkpoint=3))
        with Store(self.db) as store:
            self.assertCountEqual(
                store.rows(),
                [
                    LedgerRow('大', 9223372036854775807, 'quoted, memo\n下一行'),
                    LedgerRow('second', 9223372036854775807, ''),
                    LedgerRow('zero', 0, 'zero'),
                ],
            )
            self.assertEqual(
                store.report(), {'rows': 3, 'total_cents': 18446744073709551614}
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
        with Store(self.db) as store:
            store.connection.execute(
                """
                CREATE TRIGGER stop_second_batch BEFORE UPDATE OF checkpoint ON sources
                WHEN NEW.checkpoint = 4
                BEGIN
                    SELECT RAISE(ABORT, 'checkpoint unavailable');
                END
                """
            )
            store.connection.commit()

        with self.assertRaises(sqlite3.Error):
            import_file(self.db, self.csv_path, source_id='upload', batch_size=2)

        with Store(self.db) as store:
            self.assertEqual(store.rows(), [LedgerRow('a', 100, '')])
            self.assertEqual(store.checkpoint('upload'), 2)
            self.assertEqual(
                store.source_digest('upload'),
                hashlib.sha256(self.csv_path.read_bytes()).hexdigest(),
            )
            store.connection.execute('DROP TRIGGER stop_second_batch')
            store.connection.commit()

        result = import_file(self.db, self.csv_path, source_id='upload', batch_size=2)

        self.assertEqual(result, ImportResult(processed=3, inserted=3, checkpoint=5))
        with Store(self.db) as store:
            self.assertCountEqual(
                store.rows(),
                [
                    LedgerRow('a', 100, ''),
                    LedgerRow('b', -201, 'b'),
                    LedgerRow('c', 3, 'c'),
                    LedgerRow('d', 400, 'd'),
                ],
            )
            self.assertEqual(store.checkpoint('upload'), 5)
            self.assertEqual(store.report(), {'rows': 4, 'total_cents': 302})

        self.assertEqual(
            import_file(self.db, self.csv_path, source_id='upload'),
            ImportResult(processed=0, inserted=0, checkpoint=5),
        )

    def test_conflict_rolls_back_only_its_batch(self) -> None:
        for amount, memo in [('2.00', 'original'), ('1.00', 'changed')]:
            with self.subTest(amount=amount, memo=memo):
                db = self.root / f'conflict-{amount}-{memo}.db'
                self.write_rows(
                    [
                        ('a', '1.00', 'original'),
                        ('b', '2.00', ''),
                        ('c', '3.00', ''),
                        ('a', amount, memo),
                    ]
                )

                with self.assertRaises(ConflictError) as caught:
                    import_file(db, self.csv_path, source_id='conflict', batch_size=2)

                self.assertEqual(caught.exception.record_index, 4)
                with Store(db) as store:
                    self.assertCountEqual(
                        store.rows(),
                        [LedgerRow('a', 100, 'original'), LedgerRow('b', 200, '')],
                    )
                    self.assertEqual(store.checkpoint('conflict'), 2)

    def test_source_bytes_are_bound_even_after_first_batch_fails(self) -> None:
        self.write_rows([('a', '1.00', 'first'), ('b', 'invalid', '')])
        original_digest = hashlib.sha256(self.csv_path.read_bytes()).hexdigest()

        with self.assertRaises(SourceError) as caught:
            import_file(self.db, self.csv_path, source_id='bound', batch_size=2)

        self.assertEqual(caught.exception.record_index, 2)
        with Store(self.db) as store:
            self.assertEqual(store.rows(), [])
            self.assertEqual(store.checkpoint('bound'), 0)
            self.assertEqual(store.source_digest('bound'), original_digest)

        self.write_rows([('a', '1.00', 'first'), ('b', '2.00', '')])

        with self.assertRaises(SourceError) as changed:
            import_file(self.db, self.csv_path, source_id='bound', batch_size=2)

        self.assertEqual(changed.exception.record_index, 0)
        with Store(self.db) as store:
            self.assertEqual(store.rows(), [])
            self.assertEqual(store.checkpoint('bound'), 0)
            self.assertEqual(store.source_digest('bound'), original_digest)

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
                db = self.root / f'invalid-{index}.db'
                self.write_rows(
                    [('first', '-92233720368547758.08', 'quoted\nrecord'), row]
                )

                with self.assertRaises(SourceError) as caught:
                    import_file(db, self.csv_path, source_id='invalid', batch_size=2)

                self.assertEqual(caught.exception.record_index, 2)
                with Store(db) as store:
                    self.assertEqual(store.rows(), [])
                    self.assertEqual(store.checkpoint('invalid'), 0)

    def test_bad_headers_encoding_and_batch_sizes_fail_without_rows(self) -> None:
        documents = (
            b'external_id,amount\na,1.00\n',
            b'external_id,amount,memo,extra\na,1.00,,x\n',
            b'external_id,amount,amount\na,1.00,1.00\n',
            b'external_id,amount,memo\na,1.00,\xff\n',
        )
        for index, content in enumerate(documents):
            with self.subTest(content=content):
                db = self.root / f'file-{index}.db'
                self.csv_path.write_bytes(content)

                with self.assertRaises(SourceError) as caught:
                    import_file(db, self.csv_path, source_id='invalid-file')

                self.assertEqual(caught.exception.record_index, 0)
                with Store(db) as store:
                    self.assertEqual(store.rows(), [])

        self.write_rows([('valid', '1.00', '')])
        for batch_size in (0, -1):
            with self.subTest(batch_size=batch_size):
                absent = self.root / f'not-opened-{batch_size}.db'

                with self.assertRaises(ValueError):
                    import_file(
                        absent, self.csv_path, source_id='valid', batch_size=batch_size
                    )

                self.assertFalse(absent.exists())

    def test_cli_import_report_replay_and_failure(self) -> None:
        self.write_rows([('one', '0.10', ''), ('two', '-0.03', 'quoted, memo')])
        command = [sys.executable, '-m', 'ledgerload']
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

        imported = subprocess.run(
            [*command, *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        report = subprocess.run(
            [*command, 'report', '--db', str(self.db)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        replay = subprocess.run(
            [*command, *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        self.assertEqual(
            json.loads(imported.stdout),
            {'processed': 2, 'inserted': 2, 'checkpoint': 2},
        )
        self.assertEqual(json.loads(report.stdout), {'rows': 2, 'total_cents': 7})
        self.assertEqual(
            json.loads(replay.stdout), {'processed': 0, 'inserted': 0, 'checkpoint': 2}
        )

        self.write_rows([('one', '0.11', '')])
        rejected = subprocess.run(
            [*command, *arguments], capture_output=True, text=True, timeout=10
        )

        self.assertNotEqual(rejected.returncode, 0)
        self.assertTrue(rejected.stderr.strip())
