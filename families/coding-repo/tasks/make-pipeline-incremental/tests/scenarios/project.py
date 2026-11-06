"""Edit and build a sample project, reporting what the builds did."""

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any


def write(root: str, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def edit(root: str, name: str, old: str, new: str) -> str:
    """Replace `old` with `new` in a file, returning the content before."""
    path = Path(root) / name
    content = path.read_text()
    path.write_text(content.replace(old, new))
    return content


def append(root: str, name: str, content: str) -> None:
    with (Path(root) / name).open('a') as file:
        file.write(content)


def rewrite_keeping_status(root: str, name: str, content: str) -> None:
    """Write `content` with the file's access and modification times kept."""
    path = Path(root) / name
    status = path.stat()
    path.write_text(content)
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))


def touch(root: str, patterns: list[str], seconds: int) -> None:
    for pattern in patterns:
        for path in Path(root).glob(pattern):
            os.utime(path, (seconds, seconds))


def remove(root: str, name: str) -> None:
    (Path(root) / name).unlink()


def rename(root: str, name: str, new: str) -> None:
    (Path(root) / name).rename(Path(root) / new)


def read(root: str, name: str) -> str:
    return (Path(root) / name).read_text()


def run(
    root: Path, *args: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-m', 'pipeline', '--project', str(root), *args],
        capture_output=True,
        text=True,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **env},
        timeout=60,
    )


def cli(root: str, args: list[str], env: dict[str, str]) -> dict[str, Any]:
    """Run the command line, starting a new `runs.log`, and report the steps run."""
    log = Path(root) / 'runs.log'
    log.unlink(missing_ok=True)
    result = run(Path(root), *args, env=env)
    return {
        'returncode': result.returncode,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'runs': log.read_text().split() if log.exists() else [],
    }


def outputs(root: Path) -> dict[str, str]:
    """Read every declared output that exists, each byte as one character."""
    steps = tomllib.loads((root / 'pipeline.toml').read_text())['steps']
    return {
        output: (root / output).read_bytes().decode('latin-1')
        for step in steps.values()
        for output in step['outputs']
        if (root / output).is_file()
    }


def clean_build(root: str) -> dict[str, Any]:
    """Build a fresh copy of the project's sources, reporting both outputs."""
    project = Path(root)
    fresh = Path(tempfile.mkdtemp()) / 'fresh'
    shutil.copytree(
        project,
        fresh,
        ignore=lambda directory, names: [
            name
            for name in names
            if Path(directory) == project
            and (name.startswith('.') or name in {'build', 'site', 'runs.log'})
        ],
    )
    result = run(fresh, 'build', env={})
    return {
        'returncode': result.returncode,
        'stderr': result.stderr,
        'outputs': outputs(project),
        'fresh': outputs(fresh),
    }
