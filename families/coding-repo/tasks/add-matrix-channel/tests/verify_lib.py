"""Shared verifier helpers: copy the workspace, run unittest, and inspect sources.

Every subprocess has a timeout, and a verifier's timeouts sum to less than its
task's `[verifier] timeout_sec`, since a verifier timeout errors the trial.
"""

import argparse
import ast
import functools
import importlib.abc
import importlib.machinery
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
from collections.abc import Callable, Sequence
from typing import Any


APP = pathlib.Path(os.environ.get('BENCH_APP_DIR', '/app'))
TESTS = pathlib.Path(__file__).resolve().parent
TIMEOUT_SEC = 60
# The `add_argument` keys that a broken option with an ignored value keeps.
_KEPT_KEYS = ('default', 'dest', 'help', 'metavar', 'nargs', 'required')

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


def run_unittest(
    ws: pathlib.Path, pattern: str = 'test*.py', env: dict[str, str] | None = None
) -> tuple[bool, str]:
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
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **(env or {})},
    )
    out = proc.stdout + proc.stderr
    return proc.returncode == 0 and 'Ran 0 tests' not in out, out


def fails_on(variant: str, pattern: str) -> tuple[bool, str]:
    """Run the workspace's tests with the defect of `tests/broken/<variant>/`.

    That directory's `sitecustomize.py` breaks one part of the workspace's own
    code as Python starts, in the test run and in every Python process it starts
    with the same environment. The result is true when at least one test ran and
    the tests failed, which is what tests that catch the defect do.
    """
    pythonpath = os.pathsep.join([str(TESTS / 'broken' / variant), str(TESTS)])
    ok, out = run_unittest(copy_workspace(), pattern, {'PYTHONPATH': pythonpath})
    return not ok and 'Ran 0 tests' not in out, out


def after_import(name: str, patch: Callable[[types.ModuleType], None]) -> None:
    """Call `patch` with module `name` as soon as the module has run."""
    sys.meta_path.insert(0, _PatchingFinder(name, patch))


class _PatchingFinder(importlib.abc.MetaPathFinder):
    def __init__(self, name: str, patch: Callable[[types.ModuleType], None]) -> None:
        self.name = name
        self.patch = patch

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != self.name:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        run = spec.loader.exec_module

        def exec_module(module: types.ModuleType) -> None:
            run(module)
            self.patch(module)

        spec.loader.exec_module = exec_module
        return spec


def replace_function(owner: object, name: str, replacement: types.FunctionType) -> None:
    """Make `owner.<name>` behave as `replacement`, including through other names.

    A plain function takes the replacement's code in place, so every module that
    imported it under another name calls the replacement too.
    """
    original = getattr(owner, name, None)
    if isinstance(original, types.FunctionType) and original.__closure__ is None:
        original.__code__ = replacement.__code__
        original.__defaults__ = replacement.__defaults__
        original.__kwdefaults__ = replacement.__kwdefaults__
    else:
        setattr(owner, name, replacement)


def break_option(option: str, *, drop: bool) -> None:
    """Break `option` in every argparse parser, dropping it or ignoring its value."""
    add_argument = argparse._ActionsContainer.add_argument

    def patched(
        self: argparse._ActionsContainer, *names: str, **kwargs: Any
    ) -> argparse.Action | None:
        if option not in names:
            return add_argument(self, *names, **kwargs)
        if drop:
            return None
        kept = {key: kwargs[key] for key in _KEPT_KEYS if key in kwargs}
        return add_argument(self, *names, action=_IgnoredValue, **kept)

    argparse._ActionsContainer.add_argument = patched


class _IgnoredValue(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, self.default)


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
    """Run git on the workspace's history under a configuration of the bench's own.

    The workspace's `.git/config` and hooks could run commands of the agent's,
    through keys such as `core.fsmonitor`, so git reads a copy without them.
    """
    return subprocess.run(
        [
            'git',
            '-c',
            'safe.directory=*',
            '-c',
            'core.fsmonitor=false',
            f'--git-dir={_git_dir()}',
            f'--work-tree={APP}',
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_SEC,
    ).stdout


@functools.cache
def _git_dir() -> pathlib.Path:
    git_dir = pathlib.Path(tempfile.mkdtemp()) / 'git'
    shutil.copytree(
        APP / '.git',
        git_dir,
        symlinks=True,
        ignore=lambda directory, names: (
            {'config', 'hooks'} if directory == str(APP / '.git') else set()
        ),
    )
    (git_dir / 'config').write_text(
        '[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n'
    )
    return git_dir


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
