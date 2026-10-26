"""Run the workspace tests and the hidden behavior tests in separate scratch copies."""

import importlib.machinery
import inspect
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


APP = pathlib.Path('/app')
HIDDEN = pathlib.Path(__file__).resolve().parent / 'hidden'
TIMEOUT_SEC = 60

_failures: list[str] = []


def check(ok: bool, label: str, detail: str) -> None:
    print(('PASS ' if ok else 'FAIL ') + label, flush=True)
    if not ok:
        _failures.append(label)
        print(detail[-4000:], flush=True)


def copy_workspace() -> pathlib.Path:
    """Copy the workspace to a scratch directory without its caches and history."""
    dst = pathlib.Path(tempfile.mkdtemp()) / 'ws'
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


def run_unittest(ws: pathlib.Path) -> tuple[bool, str]:
    """Run unittest discovery under `ws/tests`, failing when no test ran."""
    proc = subprocess.run(
        [
            sys.executable,
            '-P',
            '-m',
            'unittest',
            'discover',
            '-s',
            'tests',
            '-t',
            '.',
        ],
        cwd=ws,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SEC,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
    )
    out = proc.stdout + proc.stderr
    return proc.returncode == 0 and 'Ran 0 tests' not in out, out


def check_shadowing() -> None:
    """Fail on a top-level module or package named like a standard-library one.

    `python3 -m unittest` puts the working directory first on `sys.path`, so a
    workspace's own `unittest` package would replace the runner. A directory
    without `__init__.py`, such as a virtual environment's `venv/`, is only a
    namespace portion, which loses to the standard library.
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
    ok, out = run_unittest(copy_workspace())
    check(ok, 'the workspace tests pass', out)

    ok, out = run_unittest(copy_hidden())
    check(ok, 'the hidden behavior tests pass', out)

    check_shadowing()
    print(f'{len(_failures)} check(s) failed' if _failures else 'all checks passed')
    sys.exit(1 if _failures else 0)


if __name__ == '__main__':
    main()
