import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path
from typing import Any

import sandbox


def frame(op: int, key: bytes, value: bytes = b'') -> bytes:
    body = bytes((op,)) + struct.pack('>H', len(key)) + key + value
    return struct.pack('>II', zlib.crc32(body), len(body)) + body


def segment(sequence: int, *frames: bytes) -> bytes:
    return b'TKVL' + struct.pack('>I', sequence) + b''.join(frames)


def pairs(*items: tuple[bytes, bytes]) -> list[list[str]]:
    """Return `items` as the store scenarios report them."""
    return [[key.hex(), value.hex()] for key, value in items]


class StoreContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = sandbox.scratch()

    def observe(
        self, function: str, directory: Path | None = None, **args: object
    ) -> Any:
        """Run a store scenario on `directory`, by default the test's store."""
        return sandbox.observe(
            'store_contract',
            function,
            directory=str(self.directory if directory is None else directory),
            **args,
        )

    def append(self, data: bytes) -> None:
        """Append `data` to the active segment of the test's store."""
        sandbox.observe(
            'files', 'append', directory=str(self.directory), data=data.hex()
        )

    def test_api_keeps_its_behavior(self) -> None:
        seen = self.observe('api_keeps_its_behavior')

        missing, deleted = seen['deleted']
        self.assertFalse(missing)
        self.assertTrue(deleted)
        self.assertEqual(seen['rejected'], [True, True, True])
        reopened = seen['reopened']
        self.assertEqual(reopened['items'], pairs((b'\x00binary', b''), (b'a', b'1')))
        self.assertEqual(reopened['prefixed'], pairs((b'a', b'1')))
        self.assertEqual(reopened['binary'], '')
        self.assertIsNone(reopened['missing'])
        self.assertEqual(reopened['length'], 2)
        self.assertIs(seen['closed'], True)

    def test_replays_many_segments_in_sequence_order(self) -> None:
        self.observe('fill_segments')

        count = sandbox.observe('files', 'segment_count', directory=str(self.directory))
        self.assertGreater(count, 11)
        seen = self.observe('replay_segments')
        self.assertEqual(seen['counter'], (b'39' * 8).hex())
        self.assertEqual(seen['length'], 41)

    def test_opens_a_store_written_in_the_current_format(self) -> None:
        files = {
            f'wal-{sequence}.log': segment(
                sequence,
                frame(1, b'version', str(sequence).encode()),
                frame(1, f'key-{sequence}'.encode(), b'value'),
            ).hex()
            for sequence in range(1, 13)
        }
        sandbox.observe('files', 'write', directory=str(self.directory), files=files)

        seen = self.observe('reopen_current_format')

        self.assertEqual(seen['opened'], {'version': b'12'.hex(), 'length': 13})
        self.assertEqual(seen['written'], b'13'.hex())
        self.assertEqual(seen['compacted'], {'version': b'13'.hex(), 'length': 13})

    def test_reports_damage_to_a_sealed_segment(self) -> None:
        for damage in ('flipped byte', 'truncated tail'):
            with self.subTest(damage):
                directory = self.directory / damage.replace(' ', '-')
                self.observe('seal_segments', directory)

                sandbox.observe(
                    'files', 'damage_sealed', directory=str(directory), kind=damage
                )

                raised = self.observe(
                    'opening_raises',
                    directory,
                    error='CorruptSegment',
                    segment_bytes=64,
                )
                self.assertIs(raised, True)

    def test_reports_a_damaged_snapshot(self) -> None:
        self.observe('compact_one_entry')

        sandbox.observe('files', 'damage_snapshot', directory=str(self.directory))

        self.assertIs(self.observe('opening_raises', error='CorruptSnapshot'), True)

    def test_writes_after_an_interrupted_append_survive_restarts(self) -> None:
        self.observe('store_kept')

        self.append(frame(1, b'lost', b'2')[:9])

        seen = self.observe('recover_and_put')
        self.assertIsNone(seen['error'])
        self.assertEqual(seen['recovered'], pairs((b'kept', b'1')))
        self.assertEqual(seen['reopened'], pairs((b'after', b'3'), (b'kept', b'1')))

    def test_bad_frames_in_the_active_segment_are_dropped(self) -> None:
        self.observe('store_kept')

        damaged = bytearray(frame(1, b'bad', b'checksum'))
        damaged[-1] ^= 0xFF
        self.append(bytes(damaged) + frame(1, b'after-bad', b'2'))

        seen = self.observe('recover_and_put')
        if seen['error'] is not None:
            self.fail(
                f'a bad frame in the active segment stops the store: {seen["error"]}'
            )
        self.assertEqual(seen['recovered'], pairs((b'kept', b'1')))
        self.assertEqual(seen['reopened'], pairs((b'after', b'3'), (b'kept', b'1')))

    def test_unsynced_writes_survive_a_process_crash(self) -> None:
        crashed = sandbox.start(
            'store_contract', 'crash_after_unsynced_put', directory=str(self.directory)
        )
        crashed.communicate(timeout=30)
        self.assertEqual(crashed.returncode, 0)

        self.assertEqual(self.observe('items'), pairs((b'key', b'value')))

    def test_cli_keeps_its_behavior(self) -> None:
        def run(*args: str) -> subprocess.CompletedProcess[str]:
            process = sandbox.spawn(
                [sys.executable, '-m', 'tinykv', str(self.directory), *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(timeout=10)
            return subprocess.CompletedProcess(
                process.args, process.returncode, stdout, stderr
            )

        self.assertEqual(run('put', 'b', 'two').returncode, 0)
        self.assertEqual(run('put', 'a', 'one').returncode, 0)
        self.assertEqual(run('compact').returncode, 0)
        self.assertEqual(run('delete', 'b').returncode, 0)
        self.assertEqual(run('scan').stdout, 'a\tone\n')
        missing = run('delete', 'b')
        self.assertEqual(missing.returncode, 1)
        self.assertIn('not found', missing.stderr)
