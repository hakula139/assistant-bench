"""Command-owned materialization of pinned private case sources."""

import io
import os
import re
import subprocess
import tarfile
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from harbor.models.job.config import SourceJobConfig

from assistant_bench import jobs, tasks
from assistant_bench.arena.d1 import connect
from assistant_bench.artifacts import ArtifactRepository
from assistant_bench.families import Case, Family, load_cases, load_family
from assistant_bench.messages import InputError
from assistant_bench.paths import REPO_ROOT
from assistant_bench.state import State


REPOSITORY_VAR = 'BENCH_DATA_REPOSITORY'
PUBLIC_REPOSITORY = 'https://github.com/hakula139/assistant-bench.git'


@dataclass(frozen=True)
class PrivateSources:
    revision: str
    root: Path


@dataclass(frozen=True)
class ClosingSources:
    family: Family
    cases: tuple[Case, ...]


@dataclass(frozen=True)
class RunContext:
    root: Path
    public_root: Path
    private_root: Path
    case_revision: str
    code_revision: str
    state: State
    artifacts: ArtifactRepository
    _sources: dict[str, tuple[tuple[SourceJobConfig, Path], ...]] = field(
        default_factory=dict, init=False, repr=False
    )
    _revisions: dict[str, int] = field(default_factory=dict, init=False, repr=False)

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
        generation = self.state.artifact_generation(family.name)
        self._sources[family.name] = tuple(
            jobs.materialize_sources(
                family,
                self.state,
                self.artifacts,
                self.root,
                private_root=self.private_root,
                generation=generation,
            )
        )
        self._revisions[family.name] = generation.revision

    def source_revision(self, family: Family) -> int:
        self.sources(family)
        return self._revisions[family.name]

    def closing_sources(self, collection_id: str) -> ClosingSources:
        """Materialize the exact public and private inputs frozen by collection."""
        collection = self.state.collection(collection_id)
        if collection.code_revision is None or collection.case_revision is None:
            raise InputError(
                'This collection has no verified source revisions. '
                'Collect the family again before closing a tests round.'
            )
        root = self.root / 'closing' / collection_id
        source = root / 'public'
        if not source.exists():
            materialize_implementation(collection.code_revision, source)
        private = root / 'private'
        if not private.exists():
            self.artifacts.source(collection.case_revision, private)
        family = load_family(collection.family, root=source / 'families')
        return ClosingSources(
            family=family,
            cases=tuple(load_cases(family, private_root=private / 'cases')),
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
    revision = implementation_revision()
    with tempfile.TemporaryDirectory(prefix='bench-run-') as temporary:
        root = Path(temporary)
        public = root / 'public'
        materialize_implementation(revision, public)
        with open_sources() as sources, connect('private') as database:
            database.execute('SELECT 1')
            yield RunContext(
                root=root,
                public_root=public,
                private_root=sources.root,
                case_revision=sources.revision,
                code_revision=revision,
                state=State(database),
                artifacts=ArtifactRepository(os.environ[REPOSITORY_VAR]),
            )


def implementation_revision() -> str:
    """Identify committed checkout inputs and refuse a mutable execution source."""
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
        remote = git('ls-remote', '--exit-code', PUBLIC_REPOSITORY, 'refs/heads/main')
        head, reference = remote.split('\t')
        if reference != 'refs/heads/main' or not re.fullmatch(r'[0-9a-f]{40}', head):
            raise InputError('The canonical implementation main could not be verified.')
        git('fetch', '--no-tags', '--no-write-fetch-head', PUBLIC_REPOSITORY, head)
        reachable = subprocess.run(
            [
                'git',
                '-C',
                str(REPO_ROOT),
                'merge-base',
                '--is-ancestor',
                revision,
                head,
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if reachable.returncode:
            raise InputError(
                'Runtime commands require code retained by canonical GitHub main. '
                'Merge it before creating persistent runs or collections.'
            )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InputError(
            'Runtime commands require a Git implementation checkout.'
        ) from error
    return revision


def materialize_implementation(revision: str, destination: Path) -> None:
    """Restore public implementation inputs from the canonical Git repository."""
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise InputError(
            'A collection implementation revision must be a Git commit SHA.'
        )
    environment = os.environ.copy()
    local_variables = subprocess.check_output(
        ['git', 'rev-parse', '--local-env-vars'], text=True
    ).splitlines()
    for name in local_variables:
        environment.pop(name, None)
    try:
        subprocess.run(
            [
                'git',
                '-C',
                str(REPO_ROOT),
                'fetch',
                '--no-tags',
                '--no-write-fetch-head',
                PUBLIC_REPOSITORY,
                revision,
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        archive = subprocess.check_output(
            ['git', '-C', str(REPO_ROOT), 'archive', revision],
            env=environment,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InputError(
            'The pinned implementation sources could not be materialized.'
        ) from error
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(destination, filter='data')
