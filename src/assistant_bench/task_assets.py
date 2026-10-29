"""Regenerate the shared files in the authored coding tasks."""

import argparse
from pathlib import Path

from assistant_bench.paths import REPO_ROOT


def shared_files(root: Path) -> list[Path]:
    """Return each file under `shared/`, relative to it, skipping `__pycache__`."""
    shared = root / 'families' / 'coding-repo' / 'shared'
    files = (path.relative_to(shared) for path in shared.rglob('*') if path.is_file())
    return sorted(path for path in files if '__pycache__' not in path.parts)


def sync(root: Path) -> list[Path]:
    """Return the shared-file copies this call updated."""
    family = root / 'families' / 'coding-repo'
    updated: list[Path] = []

    for shared in shared_files(root):
        content = (family / 'shared' / shared).read_bytes()
        for metadata in sorted((family / 'tasks').glob('*/task.toml')):
            destination = metadata.parent / shared
            if destination.is_file() and destination.read_bytes() == content:
                continue

            updated.append(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

    return updated


def main() -> None:
    """Copy each shared file into every authored coding task."""
    argparse.ArgumentParser(description=__doc__).parse_args()
    updated = sync(REPO_ROOT)

    for path in updated:
        print(f'Updated shared file `{path.relative_to(REPO_ROOT)}`.')


if __name__ == '__main__':
    main()
