"""Unified diffs of a task's workspace against the repository it started from."""

import difflib
import os
from pathlib import Path


CONTEXT_LINES = 3
NO_NEWLINE = '\\ No newline at end of file\n'


def workspace_diff(base: Path, workspace: Path) -> str:
    """Return the unified diff that turns `base` into `workspace`, in path order.

    Either directory may be missing, which counts as empty. Paths under a `.git`
    directory are left out. A symlink compares by its target and is never
    followed, so a link the agent planted cannot read a file outside the
    workspace. A file that is not UTF-8 on either side shows as one `Binary
    files` line.
    """
    before = _files(base)
    after = _files(workspace)
    return ''.join(
        _file_diff(path, before.get(path), after.get(path))
        for path in sorted(before.keys() | after.keys())
        if before.get(path) != after.get(path)
    )


def _files(root: Path) -> dict[str, bytes]:
    """Return the content of each file and symlink under `root`, keyed by path."""
    if not root.is_dir():
        return {}
    files = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if '.git' in relative.parts:
            continue
        if path.is_symlink():
            files[relative.as_posix()] = os.fsencode(os.readlink(path))
        elif path.is_file():
            files[relative.as_posix()] = path.read_bytes()
    return files


def _file_diff(path: str, before: bytes | None, after: bytes | None) -> str:
    old = '/dev/null' if before is None else f'a/{path}'
    new = '/dev/null' if after is None else f'b/{path}'
    try:
        old_lines = _lines(before)
        new_lines = _lines(after)
    except UnicodeDecodeError:
        return f'Binary files {old} and {new} differ\n'
    lines = list(
        difflib.unified_diff(
            old_lines, new_lines, fromfile=old, tofile=new, n=CONTEXT_LINES
        )
    )
    if not lines:
        # An empty file added or deleted has no hunk.
        return f'--- {old}\n+++ {new}\n'
    return ''.join(
        line if line.endswith('\n') else f'{line}\n{NO_NEWLINE}' for line in lines
    )


def _lines(content: bytes | None) -> list[str]:
    return [] if content is None else content.decode().splitlines(keepends=True)
