"""Unified diffs of a task's workspace against the repository it started from."""

import difflib
import io
import json
import os
import stat
from hashlib import sha256
from pathlib import Path


CONTEXT_LINES = 3
NO_NEWLINE = '\\ No newline at end of file\n'
# Git's modes for a symlink, an executable file, and any other file.
SYMLINK_MODE = '120000'
EXECUTABLE_MODE = '100755'
FILE_MODE = '100644'

LEFT_OUT = frozenset(
    {'.git', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache'}
)

type Entry = tuple[str, bytes]


def workspace_diff(base: Path, workspace: Path) -> str:
    """Return the git-style diff that turns `base` into `workspace`, in path order.

    Either directory may be missing, which counts as empty. Paths under a
    `LEFT_OUT` directory are left out, since history and tool caches record how
    the agent worked, which the vote page must not show. A symlink compares by
    its target and is never followed, so a link the agent planted cannot read a
    file outside the workspace. Each file shows its mode as git does, and a file
    that holds a NUL byte or is not UTF-8 on either side shows its content hashes
    and a `Binary files` line. Paths with control characters, quotes, or
    backslashes are quoted.
    """
    before = _entries(base)
    after = _entries(workspace)
    return ''.join(
        _file_diff(path, before.get(path), after.get(path))
        for path in sorted(before.keys() | after.keys())
        if before.get(path) != after.get(path)
    )


def _entries(root: Path) -> dict[str, Entry]:
    """Return the mode and content of each file and symlink under `root`."""
    if not root.is_dir():
        return {}

    entries = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if LEFT_OUT.intersection(relative.parts):
            continue
        if path.is_symlink():
            entries[relative.as_posix()] = (
                SYMLINK_MODE,
                os.fsencode(os.readlink(path)),
            )
        elif path.is_file():
            executable = path.stat().st_mode & stat.S_IXUSR
            entries[relative.as_posix()] = (
                EXECUTABLE_MODE if executable else FILE_MODE,
                path.read_bytes(),
            )

    return entries


def _file_diff(path: str, before: Entry | None, after: Entry | None) -> str:
    old_path = _quote_path(f'a/{path}')
    new_path = _quote_path(f'b/{path}')
    header = f'diff --git {old_path} {new_path}\n'
    if before is None:
        assert after is not None
        header += f'new file mode {after[0]}\n'
    elif after is None:
        header += f'deleted file mode {before[0]}\n'
    elif before[0] != after[0]:
        header += f'old mode {before[0]}\nnew mode {after[0]}\n'

    old_content = before[1] if before else b''
    new_content = after[1] if after else b''
    if old_content == new_content:
        return header

    old = '/dev/null' if before is None else old_path
    new = '/dev/null' if after is None else new_path
    old_lines = _lines(old_content)
    new_lines = _lines(new_content)
    if old_lines is None or new_lines is None:
        old_hash = sha256(old_content).hexdigest() if before else '0' * 64
        new_hash = sha256(new_content).hexdigest() if after else '0' * 64
        return (
            f'{header}index {old_hash}..{new_hash}\n'
            f'Binary files {old} and {new} differ\n'
        )

    lines = difflib.unified_diff(
        old_lines, new_lines, fromfile=old, tofile=new, n=CONTEXT_LINES
    )

    return header + ''.join(
        line if line.endswith('\n') else f'{line}\n{NO_NEWLINE}' for line in lines
    )


def _quote_path(path: str) -> str:
    if any(ord(character) < 32 or character in '\\"' for character in path):
        return json.dumps(path, ensure_ascii=False)
    return path


def _lines(content: bytes) -> list[str] | None:
    """Return the content's lines split at line feeds alone, or `None` for binary."""
    if b'\0' in content:
        return None
    try:
        text = content.decode()
    except UnicodeDecodeError:
        return None
    return io.StringIO(text, newline='\n').readlines()
