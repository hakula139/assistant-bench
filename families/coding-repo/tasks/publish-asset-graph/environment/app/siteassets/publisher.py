"""Publish complete immutable releases through one symlink replacement."""

import os
import shutil
import uuid
from pathlib import Path


def begin(output_root: Path) -> Path:
    releases = output_root / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    directory = releases / uuid.uuid4().hex
    directory.mkdir()
    activate(output_root, directory)
    return directory


def activate(output_root: Path, directory: Path) -> None:
    temporary = output_root / f'.current-{uuid.uuid4().hex}'

    try:
        temporary.symlink_to(
            directory.relative_to(output_root), target_is_directory=True
        )
        os.replace(temporary, output_root / 'current')
    finally:
        temporary.unlink(missing_ok=True)


def discard(directory: Path) -> None:
    shutil.rmtree(directory)
