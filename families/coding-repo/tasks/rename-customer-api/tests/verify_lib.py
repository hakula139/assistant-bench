"""Shared verifier helpers: copy the workspace, run unittest, and inspect sources.

Every subprocess has a timeout, and a verifier's timeouts sum to less than its
task's `[verifier] timeout_sec`, since a verifier timeout errors the trial.
"""

import ast
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


def fails_on(variant: str, pattern: str) -> tuple[bool, str]:
    """Run the workspace's tests with `tests/broken/<variant>/` copied over it.

    The result is true when at least one test ran and the tests failed, which is
    what tests that catch the variant's defect do.
    """
    ws = copy_workspace()
    shutil.copytree(TESTS / 'broken' / variant, ws, dirs_exist_ok=True)
    ok, out = run_unittest(ws, pattern)
    return not ok and 'Ran 0 tests' not in out, out


def parse(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return None


def calls(node: ast.AST) -> list[str]:
    """Return the names of the functions and methods called anywhere under `node`."""
    names = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                names.append(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                names.append(n.func.attr)
    return names


def count_tests(path: pathlib.Path) -> int:
    tree = parse(path)
    if tree is None:
        return 0
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name.startswith('test')
    )


def git(*args: str) -> str:
    return subprocess.run(
        ['git', '-c', 'safe.directory=*', '-C', str(APP), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_SEC,
    ).stdout


def _check_shadowing() -> None:
    """Fail on a top-level module or package named like a standard-library one.

    `python3 -m unittest` puts the working directory first on `sys.path`, so a
    workspace's own `unittest` package would replace the runner.
    """
    shadowing = sorted(
        path.name
        for path in APP.iterdir()
        if (path.is_dir() or path.suffix == '.py')
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
