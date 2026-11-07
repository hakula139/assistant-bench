"""Command-owned materialization of pinned private case sources."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from assistant_bench.artifacts import ArtifactRepository
from assistant_bench.messages import InputError


REPOSITORY_VAR = 'BENCH_DATA_REPOSITORY'


@dataclass(frozen=True)
class PrivateSources:
    revision: str
    root: Path


@contextmanager
def open_sources() -> Iterator[PrivateSources]:
    """Pin authenticated private cases for this command and remove them on exit."""
    slug = os.environ.get(REPOSITORY_VAR)
    if not slug:
        raise InputError(
            f'Set `{REPOSITORY_VAR}` to the private `owner/repository` source.'
        )
    repository = ArtifactRepository(slug)
    revision = repository.head()
    with tempfile.TemporaryDirectory(prefix='bench-sources-') as temporary:
        root = repository.source(revision, Path(temporary) / 'source')
        yield PrivateSources(revision=revision, root=root)
