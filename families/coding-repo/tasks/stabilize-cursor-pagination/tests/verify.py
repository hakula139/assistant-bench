"""Run the workspace tests and the hidden behavior tests in separate scratch copies.

Each run goes through `runner.py` in a child process, which imports the
workspace's code, and this process judges only the record the run leaves. As
root, which the verifier is in its container, it runs them as an unprivileged
user who cannot write the verifier's files or the hidden tests, and kills every
process of that user after each run and before exiting, so that nothing the
tested code started outlives verification.
"""

import ast
import contextlib
import dataclasses
import importlib.machinery
import inspect
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile


APP = pathlib.Path('/app')
TESTS = pathlib.Path(__file__).resolve().parent
HIDDEN = TESTS / 'hidden'
# Every instruction states this limit per run, and both runs must finish within
# the task's `[verifier] timeout_sec` so that a slow run still earns reward 0.
TIMEOUT_SEC = 300

# Debian's `nobody` user and `nogroup` group.
SANDBOX_ID = 65534
SANDBOXED = os.geteuid() == 0

_failures: list[str] = []


@dataclasses.dataclass(frozen=True)
class Record:
    """What a run of `runner.py` reports, with the IDs of its passed tests sorted."""

    intact: bool
    passed: list[str]
    successful: bool


def check(ok: bool, label: str, detail: str) -> None:
    print(('PASS ' if ok else 'FAIL ') + label, flush=True)
    if not ok:
        _failures.append(label)
        print(detail[-4000:], flush=True)


def copy_workspace() -> pathlib.Path:
    """Copy the workspace, without its caches and history, beside the runner."""
    root = pathlib.Path(tempfile.mkdtemp())
    root.chmod(0o755)
    shutil.copy(TESTS / 'runner.py', root)
    (root / 'runner.py').chmod(0o644)
    dst = root / 'ws'
    shutil.copytree(
        APP,
        dst,
        symlinks=True,
        ignore=shutil.ignore_patterns('__pycache__', '.git'),
    )
    return dst


def copy_hidden() -> pathlib.Path:
    """Copy the workspace with its `tests` package replaced by the hidden tests.

    The workspace's own test modules and `tests/__init__.py` never load beside
    the hidden tests, so only the application's modules run in their process.
    """
    dst = copy_workspace()
    shutil.rmtree(dst / 'tests', ignore_errors=True)
    shutil.copytree(HIDDEN, dst / 'tests')
    (dst / 'tests' / '__init__.py').touch()
    return dst


def hidden_tests() -> list[str]:
    """Return the sorted IDs of the test methods that the hidden modules define."""
    tests: list[str] = []
    for path in HIDDEN.glob('test*.py'):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ClassDef):
                tests += [
                    f'tests.{path.stem}.{node.name}.{method.name}'
                    for method in node.body
                    if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                    and method.name.startswith('test')
                ]
    return sorted(tests)


def share(ws: pathlib.Path, *, writable: bool) -> None:
    """Let the sandbox user read `ws`, and write it only when `writable`."""
    for path in [ws, *ws.rglob('*')]:
        if path.is_symlink():
            continue
        if writable:
            os.chown(path, SANDBOX_ID, SANDBOX_ID)
        else:
            readable = 0o555 if path.is_dir() else 0o444
            path.chmod((path.stat().st_mode | readable) & 0o755)


def sandbox_processes() -> list[int]:
    """Return the IDs of the live processes whose real user is the sandbox's."""
    processes: list[int] = []
    for entry in pathlib.Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        with contextlib.suppress(OSError):
            lines = (entry / 'status').read_text().splitlines()
            status = {
                name: value.split()
                for name, _, value in (line.partition(':') for line in lines)
            }
            if status['Uid'][0] == str(SANDBOX_ID) and status['State'][0] != 'Z':
                processes.append(int(entry.name))
    return processes


def kill_sandbox() -> None:
    """Kill every process of the sandbox user until none is left.

    Killing from this root process needs no new process of that user, which
    the user's process limit could refuse.
    """
    while processes := sandbox_processes():
        for pid in processes:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def stop(proc: subprocess.Popen[bytes]) -> None:
    """Kill the run and every process it left behind."""
    if SANDBOXED:
        kill_sandbox()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def run_tests(ws: pathlib.Path, *, writable: bool) -> tuple[Record | None, str]:
    """Run `runner.py` in `ws`, returning its record, if any, and its output."""
    if SANDBOXED:
        share(ws, writable=writable)
    with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as record:
        proc = subprocess.Popen(
            [sys.executable, '-I', ws.parent / 'runner.py', str(record.fileno())],
            cwd=ws,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=[record.fileno()],
            start_new_session=True,
            user=SANDBOX_ID if SANDBOXED else None,
            group=SANDBOX_ID if SANDBOXED else None,
            extra_groups=[] if SANDBOXED else None,
        )
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(TIMEOUT_SEC)
        stop(proc)

        log.seek(0)
        out = log.read().decode(errors='replace')
        record.seek(0)
        try:
            data = json.loads(record.read())
            return Record(
                intact=data['intact'] is True,
                passed=sorted(str(test) for test in data['passed']),
                successful=data['successful'] is True,
            ), out
        except KeyError, TypeError, ValueError:
            return None, out


def judge(record: Record | None, expected: list[str] | None) -> str | None:
    """Return why a run fails, or None when it passes.

    Without `expected`, as for the workspace's own tests, at least one test
    must pass and none may fail. With it, exactly those tests must pass,
    nothing else may fail, and the `unittest` machinery must be intact, since
    only the application's code could have changed it.
    """
    if record is None:
        return (
            f'The run left no record, as it exited early or ran past {TIMEOUT_SEC} s.'
        )
    if expected is None:
        if not record.successful:
            return 'A test failed.'
        return None if record.passed else 'No test passed.'
    if not record.intact:
        return 'The run changed the `unittest` machinery.'
    missing = sorted(set(expected).difference(record.passed))
    if missing:
        return 'These tests did not pass: ' + ', '.join(missing)
    if record.passed != expected:
        return 'Tests beyond the hidden test methods passed.'
    if not record.successful:
        return 'A class or module fixture failed.'
    return None


def check_tests(label: str, ws: pathlib.Path, expected: list[str] | None) -> None:
    record, out = run_tests(ws, writable=expected is None)
    reason = judge(record, expected)
    check(reason is None, label, f'{out}\n{reason}')


def check_shadowing() -> None:
    """Fail on a top-level module or package named like a standard-library one.

    Test discovery puts the workspace first on `sys.path`, so a workspace's own
    module would replace a standard-library one that loads after it. A
    directory without `__init__.py`, such as a virtual environment's `venv/`,
    is only a namespace portion, which loses to the standard library.
    """
    modules: list[str] = []
    for path in APP.iterdir():
        if path.is_dir():
            initialized = any(
                (path / f'__init__{suffix}').is_file()
                for suffix in importlib.machinery.all_suffixes()
            )
            module = path.name if initialized else None
        else:
            module = inspect.getmodulename(str(path))
        if module in sys.stdlib_module_names:
            modules.append(path.name)

    shadowing = sorted(modules)
    check(
        not shadowing,
        'no top-level module shadows the standard library',
        ', '.join(shadowing),
    )


def main() -> None:
    try:
        check_shadowing()
        check_tests('the workspace tests pass', copy_workspace(), None)
        check_tests('the hidden behavior tests pass', copy_hidden(), hidden_tests())
    finally:
        if SANDBOXED:
            kill_sandbox()
    print(f'{len(_failures)} check(s) failed' if _failures else 'all checks passed')
    sys.exit(1 if _failures else 0)


if __name__ == '__main__':
    main()
