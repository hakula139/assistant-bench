"""Command-owned materialization of pinned private case sources."""

import os
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from harbor.models.job.config import SourceJobConfig

from assistant_bench import jobs, tasks
from assistant_bench.arena.d1 import connect
from assistant_bench.artifacts import ArtifactRepository
from assistant_bench.families import Family, load_cases
from assistant_bench.messages import InputError
from assistant_bench.paths import REPO_ROOT
from assistant_bench.state import State


REPOSITORY_VAR = 'BENCH_DATA_REPOSITORY'


@dataclass(frozen=True)
class PrivateSources:
    revision: str
    root: Path


@dataclass(frozen=True)
class RunContext:
    root: Path
    private_root: Path
    case_revision: str
    code_revision: str
    state: State
    artifacts: ArtifactRepository
    _sources: dict[str, tuple[tuple[SourceJobConfig, Path], ...]] = field(
        default_factory=dict, init=False, repr=False
    )

    def sources(self, family: Family) -> list[tuple[SourceJobConfig, Path]]:
        if family.name not in self._sources:
            self.refresh_sources(family)
        return list(self._sources[family.name])

    def refresh_sources(self, family: Family) -> None:
        """Pin a new artifact inventory after an explicitly completed operation."""
        if family.source == 'cases':
            tasks.render_tasks(
                family,
                load_cases(family, private_root=self.private_root),
                root=self.root,
            )
        self._sources[family.name] = tuple(
            jobs.materialize_sources(
                family,
                self.state,
                self.artifacts,
                self.root,
                private_root=self.private_root,
            )
        )


@contextmanager
def open_sources() -> Generator[PrivateSources]:
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


@contextmanager
def open_context() -> Generator[RunContext]:
    """Open cloud state and pinned sources for one disposable local execution."""
    revision = _implementation_revision()
    with (
        open_sources() as sources,
        connect('private') as database,
        tempfile.TemporaryDirectory(prefix='bench-run-') as temporary,
    ):
        database.execute('SELECT 1')
        yield RunContext(
            root=Path(temporary),
            private_root=sources.root,
            case_revision=sources.revision,
            code_revision=revision,
            state=State(database),
            artifacts=ArtifactRepository(os.environ[REPOSITORY_VAR]),
        )


def _implementation_revision() -> str:
    try:
        variables = subprocess.check_output(
            ['git', 'rev-parse', '--local-env-vars'], text=True
        ).splitlines()
        environment = {
            name: value for name, value in os.environ.items() if name not in variables
        }

        def git(*arguments: str) -> str:
            return subprocess.check_output(
                ['git', '-C', str(REPO_ROOT), *arguments],
                env=environment,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

        root = git('rev-parse', '--show-toplevel')
        if Path(root).resolve() != REPO_ROOT.resolve():
            raise InputError('Runtime commands require an implementation checkout.')
        revision = git('rev-parse', 'HEAD')
        if git('status', '--porcelain', '--untracked-files=no'):
            raise InputError(
                'Runtime commands require committed implementation inputs. '
                'Commit the tracked changes before running this command.'
            )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InputError(
            'Runtime commands require a Git implementation checkout.'
        ) from error
    return revision
