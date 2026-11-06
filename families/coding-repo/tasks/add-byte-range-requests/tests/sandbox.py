"""Run the workspace's code as an unprivileged user, apart from the verifier.

The verifier and the hidden tests import this module and never the workspace's
code, which runs only in the child processes started here. As root, which the
verifier is in its container, those processes run as the sandbox user, who
cannot signal, trace, or write root's processes and files. A scenario returns
its observation as JSON, so only plain values reach an assertion.
"""

import contextlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from typing import Any


APP = pathlib.Path('/app')
TESTS = pathlib.Path(__file__).resolve().parent
OBSERVATION_LIMIT = 1 << 20
"""The most bytes of JSON one scenario may return."""

# Debian's `nobody` user and `nogroup` group.
SANDBOX_ID = 65534
SANDBOXED = os.geteuid() == 0

_root: pathlib.Path | None = None
_spawned: list[subprocess.Popen[Any]] = []


def copy_workspace() -> pathlib.Path:
    """Copy the workspace, without its caches and history, into a new root.

    The root holds the copy as `ws/` beside the runner, the scenario host, and
    the scenarios, and is readable by the sandbox user.
    """
    root = pathlib.Path(tempfile.mkdtemp())
    root.chmod(0o755)
    for name in ('runner.py', 'host.py'):
        shutil.copy(TESTS / name, root)
    if (TESTS / 'scenarios').is_dir():
        shutil.copytree(TESTS / 'scenarios', root / 'scenarios')
    shutil.copytree(
        APP,
        root / 'ws',
        symlinks=True,
        ignore=shutil.ignore_patterns('__pycache__', '.git'),
    )
    share(root, writable=False)
    return root / 'ws'


def share(path: pathlib.Path, *, writable: bool) -> None:
    """Let the sandbox user read `path`, and write it only when `writable`."""
    if not SANDBOXED:
        return
    for entry in [path, *path.rglob('*')]:
        if entry.is_symlink():
            continue
        if writable:
            os.chown(entry, SANDBOX_ID, SANDBOX_ID)
        else:
            readable = 0o555 if entry.is_dir() else 0o444
            entry.chmod((entry.stat().st_mode | readable) & 0o755)


def enter() -> None:
    """Copy the workspace read-only for the hidden run's scenarios."""
    global _root
    _root = copy_workspace().parent


def workspace() -> pathlib.Path:
    """Return the read-only workspace copy that `enter` made."""
    assert _root is not None, 'sandbox.enter() has not run'
    return _root / 'ws'


def scratch(*, writable: bool = True) -> pathlib.Path:
    """Return a new directory that the sandbox user may read.

    The sandbox user owns it when `writable`. Otherwise root owns it, so a test
    can place inputs there that the workspace's code may read but not replace.
    """
    path = pathlib.Path(tempfile.mkdtemp(dir=workspace().parent))
    path.chmod(0o755)
    share(path, writable=writable)
    return path


def spawn(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: os.PathLike[str] | None = None,
    **options: Any,
) -> subprocess.Popen[Any]:
    """Start `args` as the sandbox user, by default in the workspace copy.

    `options` go to `subprocess.Popen`. The process leads a new session, so
    `kill_sandbox` can end it and what it started.
    """
    sandbox = SANDBOX_ID if SANDBOXED else None
    process = subprocess.Popen(
        args,
        cwd=workspace() if cwd is None else cwd,
        start_new_session=True,
        user=sandbox,
        group=sandbox,
        extra_groups=[] if SANDBOXED else None,
        **options,
    )
    _spawned.append(process)
    return process


def host(module: str, function: str, *fd: int) -> list[str | os.PathLike[str]]:
    """Return the command that runs a scenario, writing its result to `fd`."""
    path = workspace().parent / 'host.py'
    return [sys.executable, '-I', path, module, function, *map(str, fd)]


def start(module: str, function: str, /, **args: Any) -> subprocess.Popen[str]:
    """Start `scenarios.<module>.<function>(**args)` as the sandbox user.

    The scenario reads the rest of the returned process's stdin and writes its
    stdout, both as text.
    """
    process = spawn(
        host(module, function),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(args) + '\n')
    process.stdin.flush()
    return process


def observe(module: str, function: str, /, **args: Any) -> Any:
    """Run `scenarios.<module>.<function>(**args)` as the sandbox user.

    Return the scenario's result decoded from JSON. Raise `AssertionError` with
    the scenario's output when it returns no valid observation within the
    limit.
    """
    with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as record:
        process = spawn(
            host(module, function, record.fileno()),
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=[record.fileno()],
        )
        with contextlib.suppress(BrokenPipeError):
            process.communicate(json.dumps(args).encode() + b'\n')
        stop(process)

        record.seek(0)
        data = record.read(OBSERVATION_LIMIT + 1)
        log.seek(0)
        out = log.read().decode(errors='replace')[-4000:]

    scenario = f'`{module}.{function}`'
    if len(data) > OBSERVATION_LIMIT:
        raise AssertionError(f'{scenario} returned over {OBSERVATION_LIMIT} bytes.')
    try:
        return json.loads(data)
    except ValueError:
        raise AssertionError(f'{scenario} returned no observation.\n{out}') from None


def stop(process: subprocess.Popen[Any]) -> None:
    """Kill `process` with its session and reap it."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


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
    """Kill every process the sandbox ran, until none is left.

    As root this kills every process of the sandbox user, which needs no new
    process of that user, while the user's process limit could refuse one.
    Otherwise it kills the session of each process `spawn` started.
    """
    if SANDBOXED:
        while processes := sandbox_processes():
            for pid in processes:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
    for process in _spawned:
        stop(process)
    _spawned.clear()
