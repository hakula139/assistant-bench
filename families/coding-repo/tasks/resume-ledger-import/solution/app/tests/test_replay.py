import tempfile
import unittest
from pathlib import Path

from ledgerload import ImportResult, Store, import_file


class ReplayTest(unittest.TestCase):
    def test_duplicate_records_advance_checkpoint_without_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'ledger.db'
            source = Path(directory) / 'entries.csv'
            source.write_text('external_id,amount,memo\na,1.00,same\na,1.00,same\n')

            result = import_file(db, source, source_id='replay', batch_size=1)
            repeated = import_file(db, source, source_id='replay', batch_size=1)

            self.assertEqual(result, ImportResult(2, 1, 2))
            self.assertEqual(repeated, ImportResult(0, 0, 2))
            with Store(db) as store:
                self.assertEqual(store.report(), {'rows': 1, 'total_cents': 100})
