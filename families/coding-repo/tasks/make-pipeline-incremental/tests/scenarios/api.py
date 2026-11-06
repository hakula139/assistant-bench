"""Build a sample project through the Python API, reporting what the builds did."""

import sys
from pathlib import Path
from typing import Any

import pipeline

from .project import edit


def load(root: str) -> pipeline.Pipeline:
    """Load the project for builds in this process, which caches no bytecode."""
    sys.dont_write_bytecode = True
    return pipeline.load(Path(root))


def builds(root: str, target: str) -> dict[str, Any]:
    """Build everything, then `target`."""
    messages: list[str] = []
    project = load(root)
    first = pipeline.build(project, log=messages.append)
    second = pipeline.build(project, target, log=messages.append)
    return {
        'first': {'ran': first.ran, 'skipped': first.skipped},
        'second': {'ran': second.ran, 'skipped': second.skipped},
        'messages': messages,
    }


def rejects_config(root: str) -> bool:
    """Tell whether building raises `ConfigError`."""
    try:
        pipeline.build(load(root), log=lambda _: None)
    except pipeline.ConfigError:
        return True
    return False


def edit_between_builds(root: str, name: str, old: str, new: str) -> None:
    """Build, edit a file, and build again, all in this process."""
    project = load(root)
    pipeline.build(project, log=lambda _: None)
    edit(root, name, old, new)
    pipeline.build(project, log=lambda _: None)
