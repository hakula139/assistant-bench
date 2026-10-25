"""Run bounded behavior tests in a scratch copy of the candidate workspace."""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


APP = pathlib.Path(os.environ.get('BENCH_APP_DIR', '/app'))
TESTS = pathlib.Path(__file__).resolve().parent
TIMEOUT_SEC = 60

_failures: list[str] = []


def check(ok: bool, label: str, detail: str = '') -> bool:
    print(('PASS ' if ok else 'FAIL ') + label, flush=True)
    if not ok:
        _failures.append(label)
        if detail:
            print(detail[-4000:], flush=True)
    return ok


def copy_workspace(hidden: tuple[str, ...] = ()) -> pathlib.Path:
    """Copy the workspace to a scratch directory and add the hidden test modules."""
    dst = pathlib.Path(tempfile.mkdtemp()) / 'ws'
    shutil.copytree(APP, dst, ignore=shutil.ignore_patterns('__pycache__', '.git'))
    (dst / 'tests').mkdir(exist_ok=True)
    for name in hidden:
        target = f'test_hidden_{name.removeprefix("test_")}'
        shutil.copy(TESTS / 'hidden' / name, dst / 'tests' / target)
    return dst


def run_unittest(ws: pathlib.Path, pattern: str = 'test*.py') -> tuple[bool, str]:
    """Run unittest discovery under `ws/tests`, failing when no test ran."""
    proc = subprocess.run(
        [
            sys.executable,
            '-m',
            'unittest',
            'discover',
            '-s',
            'tests',
            '-t',
            '.',
            '-p',
            pattern,
        ],
        cwd=ws,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SEC,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
    )
    out = proc.stdout + proc.stderr
    return proc.returncode == 0 and 'Ran 0 tests' not in out, out


def _check_shadowing() -> None:
    """Fail on a top-level module or package named like a standard-library one.

    `python3 -m unittest` puts the working directory first on `sys.path`, so a
    workspace's own `unittest` package would replace the runner. A directory
    without `__init__.py`, such as a virtual environment's `venv/`, is only a
    namespace portion, which loses to the standard library.
    """
    shadowing = sorted(
        path.name
        for path in APP.iterdir()
        if (path.suffix == '.py' or (path / '__init__.py').is_file())
        and path.stem in sys.stdlib_module_names
    )
    check(
        not shadowing,
        'no top-level module shadows the standard library',
        ', '.join(shadowing),
    )


def finish() -> None:
    _check_shadowing()
    print(f'{len(_failures)} check(s) failed' if _failures else 'all checks passed')
    sys.exit(1 if _failures else 0)
