"""Write source trees, build them, and report the published files."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from siteassets import BuildError, build as build_assets


ROOT = Path(__file__).resolve().parents[1]


def write(root: str, files: dict[str, str], links: dict[str, str]) -> None:
    """Write each file, then point each link at its target."""
    for name, content in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
    for name, target in links.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


def build(source: str, output: str) -> dict[str, Any]:
    try:
        result = build_assets(Path(source), Path(output))
    except BuildError as error:
        return {'error': repr(error)}
    return {
        'error': None,
        'directory': str(result.directory),
        'manifest': result.manifest,
    }


def limited_build(source: str, output: str, limit: int) -> dict[str, Any]:
    """Build in another process whose output files may hold `limit` bytes."""
    completed = subprocess.run(
        [sys.executable, '-m', 'scenarios.limited_build', source, output, str(limit)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={**os.environ, 'PYTHONPATH': str(ROOT)},
    )
    return {
        'returncode': completed.returncode,
        'stdout': completed.stdout,
        'stderr': completed.stderr,
    }


def cli(source: str, output: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, '-m', 'siteassets', source, output],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return {'returncode': completed.returncode, 'stderr': completed.stderr}


def entry(path: str) -> dict[str, Any]:
    """Report whether `path` is a file, where it links, and where it resolves."""
    location = Path(path)
    return {
        'is_file': location.is_file(),
        'link': str(location.readlink()) if location.is_symlink() else None,
        'resolved': str(location.resolve()),
    }


def read(path: str) -> str:
    """Return the bytes of `path` as hexadecimal."""
    return Path(path).read_bytes().hex()


def files(root: str) -> dict[str, str]:
    """Return the hexadecimal bytes of each file under `root`, by relative path."""
    directory = Path(root)
    return {
        path.relative_to(directory).as_posix(): path.read_bytes().hex()
        for path in directory.rglob('*')
        if path.is_file()
    }


def glob(root: str, pattern: str) -> list[str]:
    return [str(path) for path in Path(root).rglob(pattern)]
