"""Run Harbor Smoke on coding tasks affected by a pull request."""

import argparse
import subprocess
from pathlib import Path


RUNTIME_FILES = {
    '.github/workflows/ci.yml',
    '.python-version',
    'families/coding-repo/family.toml',
    'flake.lock',
    'flake.nix',
    'pyproject.toml',
    'systems.toml',
    'uv.lock',
}
RUNTIME_DIRS = ('.github/actions/setup-nix/', 'src/assistant_bench/')
TASKS_DIR = Path('families/coding-repo/tasks')


def selected_tasks(base: str | None) -> list[str] | None:
    """Return current changed task IDs, or None when every task needs checking."""
    if base is None:
        return None

    changed = subprocess.run(
        ['git', 'diff', '--name-only', '-z', '--no-renames', base, 'HEAD', '--'],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.split('\0')

    if any(path in RUNTIME_FILES or path.startswith(RUNTIME_DIRS) for path in changed):
        return None

    return sorted(
        {
            parts[3]
            for path in changed
            if len(parts := Path(path).parts) >= 4
            and Path(*parts[:3]) == TASKS_DIR
            and (TASKS_DIR / parts[3]).is_dir()
        }
    )


def main() -> None:
    """Run all coding tasks, or only those changed since the optional base."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', nargs='?')
    args = parser.parse_args()
    tasks = selected_tasks(args.base)
    if tasks == []:
        print('No coding tasks changed.')
        return

    command = ['bench', 'check', 'coding-repo']
    for task in tasks or []:
        command.extend(['--task', task])

    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
