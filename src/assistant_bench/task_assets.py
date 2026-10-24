"""Regenerate the common standalone files in the authored coding tasks."""

import argparse
from pathlib import Path

from assistant_bench.paths import REPO_ROOT


ASSETS = ('environment/Dockerfile', 'tests/test.sh', 'tests/verify_lib.py')


def sync(root: Path, *, check: bool = False) -> list[Path]:
    """Return stale artifact paths, updating them unless `check` is set."""
    family = root / 'families' / 'coding-repo'
    stale: list[Path] = []

    for asset in ASSETS:
        content = (family / 'shared' / asset).read_bytes()
        for metadata in sorted((family / 'tasks').glob('*/task.toml')):
            destination = metadata.parent / asset
            if (
                not destination.is_symlink()
                and destination.is_file()
                and destination.read_bytes() == content
            ):
                continue

            stale.append(destination)
            if not check:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.is_symlink():
                    destination.unlink()

                destination.write_bytes(content)

    return stale


def main() -> None:
    """Synchronize standalone artifacts, or report stale paths without writing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    stale = sync(REPO_ROOT, check=args.check)

    for path in stale:
        print(f'{"Stale" if args.check else "Updated"}: {path.relative_to(REPO_ROOT)}')

    if args.check and stale:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
