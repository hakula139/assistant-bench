"""Regenerate the shared files in the authored coding tasks."""

import argparse
import subprocess
from pathlib import Path

from assistant_bench.paths import REPO_ROOT


FAMILY = Path('families') / 'coding-repo'


def shared_files(root: Path) -> list[Path]:
    """Return each file under `shared/` that git does not ignore, relative to it."""
    shared = root / FAMILY / 'shared'
    listed = _git_paths(
        shared, 'ls-files', '--cached', '--others', '--exclude-standard'
    )
    return sorted(path for path in listed if (shared / path).is_file())


def sync(root: Path) -> tuple[list[Path], list[Path]]:
    """Return the shared-file copies this call updated and those it removed.

    A copy is removed once its file, present under `shared/` at `HEAD`, has left
    the working tree.
    """
    family = root / FAMILY
    tasks = sorted(
        metadata.parent for metadata in (family / 'tasks').glob('*/task.toml')
    )
    current = shared_files(root)
    updated: list[Path] = []
    for shared in current:
        content = (family / 'shared' / shared).read_bytes()
        for task in tasks:
            destination = task / shared
            if destination.is_file() and destination.read_bytes() == content:
                continue

            updated.append(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

    committed = _git_paths(family / 'shared', 'ls-tree', '-r', '--name-only', 'HEAD')
    removed: list[Path] = []
    for shared in sorted(set(committed) - set(current)):
        for task in tasks:
            destination = task / shared
            if destination.is_file():
                removed.append(destination)
                destination.unlink()

    return updated, removed


def _git_paths(directory: Path, *args: str) -> list[Path]:
    listed = subprocess.run(
        ['git', *args, '-z'],
        cwd=directory,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    return [Path(path) for path in listed.split('\0') if path]


def main() -> None:
    """Copy each shared file into every authored coding task."""
    argparse.ArgumentParser(description=__doc__).parse_args()
    updated, removed = sync(REPO_ROOT)

    for path in updated:
        print(f'Updated shared file `{path.relative_to(REPO_ROOT)}`.')
    for path in removed:
        print(f'Removed stale shared file `{path.relative_to(REPO_ROOT)}`.')


if __name__ == '__main__':
    main()
