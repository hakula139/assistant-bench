import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRASH_STATUS = 86
PROBE = {b'probe-after-recovery'.hex(): b'still here'.hex()}
SEGMENT_BYTES = 96

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
        first, second = result.stdout.splitlines()
        return json.loads(first), json.loads(second)

    def check_every_crash(self, setup: Ops, ops: Ops) -> None:
        """Crash before each mutation `ops` makes, and halfway through each write."""
        base = self.root / 'base'
        self.run_ops(base, setup)
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


if __name__ == '__main__':
    unittest.main()
