"""Run the workspace tests in a scratch copy, then the hidden behavior tests.

The workspace run goes through `runner.py` in a child process, which imports
the workspace's code, and this process judges only the record the run leaves.
The hidden tests run in this process, which never imports the workspace's code,
and reach it only through the scenarios and processes that `sandbox` runs as
the sandbox user. As root, `verify.py` first makes this directory unreadable to
that user, and kills every process of the user after each run and test and
before exiting, so that nothing the tested code started outlives verification.
"""

import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import sys
import tempfile
import types
import unittest

import sandbox


HIDDEN = sandbox.TESTS / 'hidden'
# Every instruction states this limit per run, and both runs must finish within
# the task's `[verifier] timeout_sec` so that a slow run still earns reward 0.
TIMEOUT_SEC = 300

_failures: list[str] = []


@dataclasses.dataclass(frozen=True)
class Record:
    """What a run of `runner.py` reports, with the IDs of its passed tests sorted."""

    passed: list[str]
    successful: bool


class IsolatingResult(unittest.TextTestResult):
    """Kill whatever the sandbox ran once each test ends."""

    def stopTest(self, test: unittest.TestCase) -> None:
        sandbox.kill_sandbox()
        super().stopTest(test)


class IsolatingRunner(unittest.TextTestRunner):
    # unittest documents this override, while typeshed rejects a subclass of
    # `TextTestResult` as `resultclass`.
    def _makeResult(self) -> IsolatingResult:
        return IsolatingResult(
            self.stream, self.descriptions, self.verbosity, durations=self.durations
        )


def check(ok: bool, label: str, detail: str) -> None:
    print(('PASS ' if ok else 'FAIL ') + label, flush=True)
    if not ok:
        _failures.append(label)
        print(detail[-4000:], flush=True)


def lock_tests() -> None:
    """Leave this directory readable only by root."""
    for path in [sandbox.TESTS, *sandbox.TESTS.rglob('*')]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & 0o700)


def run_workspace_tests() -> tuple[Record | None, str]:
    """Run `runner.py` in a writable workspace copy, for its record and output."""
    ws = sandbox.copy_workspace()
    sandbox.share(ws, writable=True)
    with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as record:
        process = sandbox.spawn(
            [sys.executable, '-I', ws.parent / 'runner.py', str(record.fileno())],
            cwd=ws,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=[record.fileno()],
        )
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(TIMEOUT_SEC)
        sandbox.kill_sandbox()

        out = sandbox.tail(log)
        record.seek(0)
        try:
            data = json.loads(record.read())
            return Record(
                passed=sorted(str(test) for test in data['passed']),
                successful=data['successful'] is True,
            ), out
        except KeyError, TypeError, ValueError:
            return None, out


def judge(record: Record | None) -> str | None:
    """Return why the workspace run fails, or None when it passes.

    At least one test must pass and none may fail.
    """
    if record is None:
        return (
            f'The run left no record, as it exited early or ran past {TIMEOUT_SEC} s.'
        )
    if not record.successful:
        return 'A test failed.'
    return None if record.passed else 'No test passed.'


def time_out(number: int, frame: types.FrameType | None) -> None:
    sandbox.kill_sandbox()
    check(False, 'the hidden behavior tests pass', f'The run ran past {TIMEOUT_SEC} s.')
    os._exit(1)


def check_hidden_tests() -> None:
    """Run every hidden test, which must pass with none skipped."""
    sandbox.enter(TIMEOUT_SEC)
    signal.signal(signal.SIGALRM, time_out)
    signal.alarm(TIMEOUT_SEC)
    suite = unittest.TestLoader().discover(
        str(HIDDEN), top_level_dir=str(sandbox.TESTS)
    )
    result = IsolatingRunner(stream=sys.stdout, verbosity=2).run(suite)
    signal.alarm(0)

    if not result.testsRun:
        reason = 'No hidden test ran.'
    elif result.skipped:
        reason = 'A hidden test was skipped.'
    elif not result.wasSuccessful():
        reason = 'A hidden test failed.'
    else:
        reason = None
    check(reason is None, 'the hidden behavior tests pass', str(reason))


def main() -> None:
    if sandbox.SANDBOXED:
        lock_tests()
    try:
        record, out = run_workspace_tests()
        reason = judge(record)
        check(reason is None, 'the workspace tests pass', f'{out}\n{reason}')
        check_hidden_tests()
    finally:
        sandbox.kill_sandbox()
    print(f'{len(_failures)} check(s) failed' if _failures else 'all checks passed')
    sys.exit(1 if _failures else 0)


if __name__ == '__main__':
    main()
