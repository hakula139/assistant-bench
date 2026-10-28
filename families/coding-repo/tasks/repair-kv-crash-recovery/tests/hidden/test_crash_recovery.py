import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRASH_STATUS = 86
PROBE = {b'probe-after-recovery'.hex(): b'still here'.hex()}
SEGMENT_BYTES = 96
TORN_FRAME = struct.pack('>II', 0, 9) + b'\x01\x00'
"""The start of a frame whose write a crash cut short."""

type State = dict[str, str]
type Ops = list[list[str]]


def put(key: str, value: str) -> list[str]:
    return ['put', key.encode().hex(), value.encode().hex()]


def delete(key: str) -> list[str]:
    return ['delete', key.encode().hex()]


COMPACT = ['compact']


def states(ops: Ops, initial: State | None = None) -> list[State]:
    """Return the contents before the operations and after each one."""
    state = dict(initial or {})
    history = [dict(state)]
    for name, *operands in ops:
        if name == 'put':
            state[operands[0]] = operands[1]
        elif name == 'delete':
            state.pop(operands[0], None)
        history.append(dict(state))

    return history


def driver(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-m', 'tests.crashsim', *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
    )


class CrashRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_ops(self, directory: Path, ops: Ops, *crash: str) -> list[str]:
        result = driver(
            'run', str(directory), str(SEGMENT_BYTES), json.dumps(ops), *crash
        )
        expected = CRASH_STATUS if crash else 0
        self.assertEqual(result.returncode, expected, result.stderr)
        return result.stdout.splitlines()

    def recover(self, directory: Path, label: str) -> tuple[State, State]:
        result = driver('recover', str(directory), str(SEGMENT_BYTES))
        self.assertEqual(result.returncode, 0, f'{label}: {result.stderr}')
        first, second = (
            json.loads(line.removeprefix('entries '))
            for line in result.stdout.splitlines()
            if line.startswith('entries ')
        )
        return first, second

    def check_every_crash(
        self, setup: Ops, ops: Ops, damage: Callable[[Path], None] | None = None
    ) -> None:
        """Crash before each mutation opening the store and `ops` make.

        Writes also crash halfway. `damage` alters the store before the crashes.
        """
        base = self.root / 'base'
        self.run_ops(base, setup)
        if damage is not None:
            damage(base)
        expected = states(ops, states(setup)[-1])

        counted = self.root / 'counted'
        shutil.copytree(base, counted)
        mutations = [
            line.removeprefix('mutation ')
            for line in self.run_ops(counted, ops)
            if line.startswith('mutation ')
        ]
        self.assertEqual(self.recover(counted, 'no crash')[0], expected[-1])
        self.assertTrue(mutations)

        for number, kind in enumerate(mutations, 1):
            for tear in (False, True) if kind == 'write' else (False,):
                label = f'crash at mutation {number} ({kind}{", torn" if tear else ""})'
                with self.subTest(label):
                    case = self.root / f'case-{number}-{tear}'
                    shutil.copytree(base, case)
                    lines = self.run_ops(
                        case, ops, str(number), *(['tear'] if tear else [])
                    )
                    finished = sum(line.startswith('done ') for line in lines)

                    recovered, probed = self.recover(case, label)

                    allowed = expected[finished : finished + 2]
                    self.assertIn(recovered, allowed, label)
                    self.assertEqual(probed, recovered | PROBE, label)

    def test_crashes_while_writing_and_rotating_segments(self) -> None:
        setup = [put('alpha', 'one'), put('beta', 'two')]
        ops = [
            put('gamma', 'three' * 4),
            put('alpha', 'uno' * 6),
            delete('beta'),
            put('delta', 'four' * 5),
            put('epsilon', 'five' * 5),
        ]

        self.check_every_crash(setup, ops)

    def test_crashes_during_a_first_compaction(self) -> None:
        setup = [
            put('alpha', 'one' * 6),
            put('beta', 'two' * 6),
            put('gamma', 'three' * 4),
            delete('alpha'),
            put('beta', 'deux' * 5),
        ]

        self.check_every_crash(setup, [COMPACT])

    def test_crashes_during_a_compaction_that_replaces_a_snapshot(self) -> None:
        setup = [
            put('alpha', 'one' * 6),
            put('beta', 'two' * 6),
            COMPACT,
            delete('alpha'),
            put('gamma', 'three' * 4),
            put('beta', 'deux' * 5),
        ]

        self.check_every_crash(setup, [COMPACT, put('delta', 'four')])

    def test_crashes_while_recovering_a_torn_tail(self) -> None:
        def tear(directory: Path) -> None:
            active = max(
                directory.glob('wal-*.log'), key=lambda path: int(path.stem[4:])
            )
            with active.open('ab') as file:
                file.write(TORN_FRAME)

        setup = [put('alpha', 'one'), put('beta', 'two')]

        self.check_every_crash(setup, [put('gamma', 'three')], tear)

    def test_operations_sync_their_frames(self) -> None:
        ops = [put('alpha', 'one' * 20), put('beta', 'two' * 20), delete('alpha')]

        lines = self.run_ops(self.root / 'synced', ops)

        events = lines[lines.index('opened') + 1 :]
        for index in range(len(ops)):
            end = events.index(f'done {index}')
            window, events = events[:end], events[end + 1 :]
            writes = [
                at for at, event in enumerate(window) if event == 'mutation write'
            ]
            with self.subTest(operation=index):
                self.assertTrue(writes, window)
                self.assertIn('sync file', window[writes[-1] + 1 :], window)


if __name__ == '__main__':
    unittest.main()
