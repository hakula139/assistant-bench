import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from tinykv import CorruptSegment, CorruptSnapshot, Store, StoreClosed


def frame(op: int, key: bytes, value: bytes = b'') -> bytes:
    body = bytes((op,)) + struct.pack('>H', len(key)) + key + value
    return struct.pack('>II', zlib.crc32(body), len(body)) + body


def segment(sequence: int, *frames: bytes) -> bytes:
    return b'TKVL' + struct.pack('>I', sequence) + b''.join(frames)


def segments(directory: Path) -> list[Path]:
    """Return the store's segments in sequence order."""
    return sorted(directory.glob('wal-*.log'), key=lambda path: int(path.stem[4:]))


class StoreContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_api_keeps_its_behavior(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'\x00binary', b'')
            store.put(b'b', b'2')
            store.put(b'a', b'1')
            self.assertFalse(store.delete(b'missing'))
            self.assertTrue(store.delete(b'b'))
            with self.assertRaises(ValueError):
                store.put(b'k' * 65536, b'v')
            with self.assertRaises(ValueError):
                store.put(b'k', b'v' * (16 * 1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                Store.open(self.directory / 'other', segment_bytes=63)

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'\x00binary', b''), (b'a', b'1')])
            self.assertEqual(store.items(b'a'), [(b'a', b'1')])
            self.assertEqual(store.get(b'\x00binary'), b'')
            self.assertIsNone(store.get(b'b'))
            self.assertEqual(len(store), 2)

        with self.assertRaises(StoreClosed):
            store.items()

    def test_replays_many_segments_in_sequence_order(self) -> None:
        with Store.open(self.directory, segment_bytes=64) as store:
            for index in range(40):
                store.put(b'counter', str(index).encode() * 8)
                store.put(f'key-{index:02}'.encode(), b'x' * 16)

        self.assertGreater(len(segments(self.directory)), 11)
        with Store.open(self.directory, segment_bytes=64) as store:
            self.assertEqual(store.get(b'counter'), b'39' * 8)
            self.assertEqual(len(store), 41)

    def test_opens_a_store_written_in_the_current_format(self) -> None:
        for sequence in range(1, 13):
            data = segment(
                sequence,
                frame(1, b'version', str(sequence).encode()),
                frame(1, f'key-{sequence}'.encode(), b'value'),
            )
            (self.directory / f'wal-{sequence}.log').write_bytes(data)

        with Store.open(self.directory) as store:
            self.assertEqual(store.get(b'version'), b'12')
            self.assertEqual(len(store), 13)
            store.put(b'version', b'13')

        with Store.open(self.directory) as store:
            self.assertEqual(store.get(b'version'), b'13')
            store.compact()

        with Store.open(self.directory) as store:
            self.assertEqual(store.get(b'version'), b'13')
            self.assertEqual(len(store), 13)

    def test_reports_damage_to_a_sealed_segment(self) -> None:
        for damage in ('flipped byte', 'truncated tail'):
            with self.subTest(damage):
                directory = self.directory / damage.replace(' ', '-')
                with Store.open(directory, segment_bytes=64) as store:
                    for index in range(8):
                        store.put(f'key-{index}'.encode(), b'v' * 24)

                sealed = segments(directory)[0]
                data = bytearray(sealed.read_bytes())
                if damage == 'flipped byte':
                    data[-2] ^= 0xFF
                else:
                    del data[-3:]
                sealed.write_bytes(bytes(data))

                with self.assertRaises(CorruptSegment):
                    Store.open(directory, segment_bytes=64)

    def test_reports_a_damaged_snapshot(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'key', b'value')
            store.compact()

        snapshot = self.directory / 'snapshot.db'
        data = bytearray(snapshot.read_bytes())
        data[len(data) // 2] ^= 0xFF
        snapshot.write_bytes(bytes(data))

        with self.assertRaises(CorruptSnapshot):
            Store.open(self.directory)

    def test_writes_after_an_interrupted_append_survive_restarts(self) -> None:
        with Store.open(self.directory) as store:
            store.put(b'kept', b'1')

        active = segments(self.directory)[-1]
        with active.open('ab') as file:
            file.write(frame(1, b'lost', b'2')[:9])

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'kept', b'1')])
            store.put(b'after', b'3')

        with Store.open(self.directory) as store:
            self.assertEqual(store.items(), [(b'after', b'3'), (b'kept', b'1')])

    def test_cli_keeps_its_behavior(self) -> None:
        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, '-m', 'tinykv', str(self.directory), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

        self.assertEqual(run('put', 'b', 'two').returncode, 0)
        self.assertEqual(run('put', 'a', 'one').returncode, 0)
        self.assertEqual(run('compact').returncode, 0)
        self.assertEqual(run('delete', 'b').returncode, 0)
        self.assertEqual(run('scan').stdout, 'a\tone\n')
        missing = run('delete', 'b')
        self.assertEqual(missing.returncode, 1)
        self.assertIn('not found', missing.stderr)


if __name__ == '__main__':
    unittest.main()
