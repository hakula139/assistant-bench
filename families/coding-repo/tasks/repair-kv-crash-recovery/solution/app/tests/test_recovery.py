import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tinykv import CorruptSegment, Store, StoreClosed
from tinykv.codec import Op, Record, encode


def segments(directory: Path) -> list[Path]:
    return sorted(directory.glob('wal-*.log'), key=lambda path: int(path.stem[4:]))


class RecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_appends_after_a_torn_record_survive_the_next_restart(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'kept', b'1')
        with segments(self.directory)[-1].open('ab') as file:
            file.write(encode(Record(Op.PUT, b'torn', b'2'))[:-1])

        with Store.open(self.directory) as store:
            store.put(b'after', b'3')

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'after', b'3'), (b'kept', b'1')])

    def test_a_torn_segment_header_counts_as_an_empty_segment(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'kept', b'1')
        (self.directory / 'wal-2.log').write_bytes(b'TKV')

        with Store.open(self.directory) as store:
            store.put(b'after', b'2')

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'after', b'2'), (b'kept', b'1')])

    def test_damage_to_a_sealed_segment_is_reported(self) -> None:
        with Store.open(self.directory, segment_bytes=64) as store:
            for index in range(4):
                store.put(f'key-{index}'.encode(), b'v' * 24)
        sealed = segments(self.directory)[0]
        sealed.write_bytes(sealed.read_bytes()[:-1])

        with self.assertRaises(CorruptSegment):
            Store.open(self.directory, segment_bytes=64)

    def test_closed_stores_refuse_every_write(self) -> None:
        store = Store.open(self.directory)
        store.close()

        for key, value in ((b'key', b'value'), (b'', b'value')):
            with self.subTest(key=key), self.assertRaises(StoreClosed):
                store.put(key, value)

    def test_segments_replay_in_numeric_order(self) -> None:
        with Store.open(self.directory, segment_bytes=64) as store:
            for index in range(12):
                store.put(b'key', str(index).encode() * 30)

        with Store.open(self.directory, segment_bytes=64) as store:
            self.assertEqual(store.get(b'key'), b'11' * 30)

    def test_an_interrupted_compaction_keeps_the_previous_snapshot(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'a', b'1')
            store.compact()
            store.delete(b'a')
            store.put(b'b', b'2')
            with (
                mock.patch.object(Path, 'replace', side_effect=OSError('crash')),
                self.assertRaises(OSError),
            ):
                store.compact()

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'b', b'2')])

    def test_compaction_survives_a_leftover_temporary_snapshot(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'a', b'1')
        (self.directory / 'snapshot.db.tmp').write_bytes(os.urandom(32))

        with Store.open(self.directory) as store:
            store.compact()

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'a', b'1')])


if __name__ == '__main__':
    unittest.main()
