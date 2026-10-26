"""Durability helpers for files and directories."""

import os
from pathlib import Path
from typing import BinaryIO


def sync_file(file: BinaryIO) -> None:
    """Flush Python's buffer and ask the kernel to persist the file's bytes."""
    file.flush()
    os.fsync(file.fileno())


def sync_directory(directory: Path) -> None:
    """Persist the directory entries, such as a newly created file's name."""
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
