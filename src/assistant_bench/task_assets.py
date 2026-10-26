"""Regenerate the shared files in the authored coding tasks."""

from pathlib import Path

from assistant_bench.paths import REPO_ROOT


SHARED_FILES = ('environment/Dockerfile', 'tests/test.sh', 'tests/verify_lib.py')


def sync(root: Path) -> list[Path]:
    """Return the shared-file copies this call updated."""
    family = root / 'families' / 'coding-repo'
    updated: list[Path] = []

    for shared in SHARED_FILES:
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
    updated = sync(REPO_ROOT)

    for path in updated:
        print(f'Updated shared file `{path.relative_to(REPO_ROOT)}`.')


if __name__ == '__main__':
    main()
