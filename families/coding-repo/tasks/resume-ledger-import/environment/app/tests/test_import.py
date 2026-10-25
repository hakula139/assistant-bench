import sqlite3
import tempfile
import unittest
from pathlib import Path

from ledgerload import LedgerRow, Store, import_file


class ImportFileTest(unittest.TestCase):
    def test_checkpoint_failure_does_not_leave_a_partial_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'ledger.db'
            source = Path(directory) / 'entries.csv'
            source.write_text('external_id,amount,memo\na,1.00,first\nb,2.00,second\n')
            with Store(db) as store:
                store.connection.execute(
                    """
                    CREATE TRIGGER reject_checkpoint
                    BEFORE UPDATE OF checkpoint ON sources
                    BEGIN
                        SELECT RAISE(ABORT, 'checkpoint unavailable');
                    END
                    """
                )
                store.connection.commit()

            with self.assertRaises(sqlite3.Error):
                import_file(db, source, source_id='visible', batch_size=2)

            with Store(db) as store:
                self.assertEqual(store.rows(), [])
                self.assertEqual(store.checkpoint('visible'), 0)
                store.connection.execute('DROP TRIGGER reject_checkpoint')
                store.connection.commit()

            result = import_file(db, source, source_id='visible', batch_size=2)

            self.assertEqual(
                (result.processed, result.inserted, result.checkpoint), (2, 2, 2)
            )
            with Store(db) as store:
                self.assertCountEqual(
                    store.rows(),
                    [LedgerRow('a', 100, 'first'), LedgerRow('b', 200, 'second')],
                )
