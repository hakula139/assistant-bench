import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tinykv import Store, StoreClosed


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_entries_survive_reopening(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'b', b'2')
            store.put(b'a', b'1')
            store.put(b'b', b'3')
            self.assertTrue(store.delete(b'a'))
            self.assertFalse(store.delete(b'missing'))

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'b', b'3')])
            self.assertIsNone(store.get(b'a'))

    def test_items_filter_by_prefix_in_key_order(self) -> None:
        with Store.open(self.directory) as store:
            for key in (b'user:2', b'group:1', b'user:1'):
                store.put(key, key.upper())

            self.assertEqual(
                store.items(b'user:'),
                [(b'user:1', b'USER:1'), (b'user:2', b'USER:2')],
            )
            self.assertEqual(len(store), 3)

    def test_small_segments_rotate_and_compact(self) -> None:
        with Store.open(self.directory, segment_bytes=64) as store:
            for index in range(6):
                store.put(f'key-{index}'.encode(), b'x' * 20)

        self.assertGreater(len(list(self.directory.glob('wal-*.log'))), 1)

        with Store.open(self.directory, segment_bytes=64) as store:
            store.compact()

        self.assertEqual(len(list(self.directory.glob('wal-*.log'))), 1)
        with Store.open(self.directory, segment_bytes=64) as store:
            self.assertEqual(len(store), 6)

    def test_rejects_invalid_keys_and_closed_use(self) -> None:
        store = Store.open(self.directory)
        with self.assertRaises(ValueError):
            store.put(b'', b'value')

        store.close()
        with self.assertRaises(StoreClosed):
            store.get(b'key')

    def test_cli_round_trip(self) -> None:
        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, '-m', 'tinykv', str(self.directory), *args],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(run('put', 'greeting', 'hello').returncode, 0)
        self.assertEqual(run('get', 'greeting').stdout, 'hello\n')
        self.assertEqual(run('scan').stdout, 'greeting\thello\n')
        self.assertEqual(run('get', 'missing').returncode, 1)


if __name__ == '__main__':
    unittest.main()
