"""Immutable private Git sources and trials through authenticated `gh` and Git."""

import io
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from assistant_bench.diffs import LEFT_OUT
from assistant_bench.messages import InputError


type GitMode = Literal['100644', '100755', '120000']
type Commit = Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
type Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
type Component = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]

JOB_FILES = (
    'config.json',
    'lock.json',
    'result.json',
    'attempts.jsonl',
    'job.log',
    'exception.txt',
)
TRIAL_FILES = ('config.json', 'lock.json', 'result.json', 'trial.log', 'exception.txt')
TRIAL_DIRECTORIES = ('agent', 'verifier', 'artifacts')
GIT_MODES: tuple[str, ...] = ('100644', '100755', '120000')


# ── Models ──


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class FileEntry(_Model):
    path: str
    mode: GitMode
    sha256: Digest

    @model_validator(mode='after')
    def _relative_path(self) -> Self:
        _path_parts(self.path)
        return self


class ArtifactRef(_Model):
    commit_sha: Commit
    path: str
    manifest_digest: Digest

    @model_validator(mode='after')
    def _trial_path(self) -> Self:
        parts = _path_parts(self.path)
        if len(parts) != 4 or parts[0] != 'runs' or parts[2] != 'trials':
            raise ValueError('an artifact reference names runs/<run>/trials/<artifact>')
        return self


class TrialManifest(_Model):
    """Preserve unknown original execution pins as null revisions."""

    scope: Component
    run_id: Component
    artifact_id: Component
    trial_id: Component
    job_name: Component
    trial_name: Component
    code_revision: Commit | None
    case_revision: Commit | None
    files: tuple[FileEntry, ...]

    @property
    def path(self) -> str:
        return f'runs/{self.run_id}/trials/{self.artifact_id}'

    def content(self) -> bytes:
        """Return the canonical UTF-8 bytes identified by the manifest digest."""
        return f'{self.model_dump_json(indent=2)}\n'.encode()

    @model_validator(mode='after')
    def _complete_trial(self) -> Self:
        paths = [entry.path for entry in self.files]
        if paths != sorted(set(paths)):
            raise ValueError('manifest files must be unique and sorted by path')
        required = {
            'job/config.json',
            *(
                f'job/{self.trial_name}/{name}'
                for name in ('config.json', 'lock.json', 'result.json')
            ),
        }
        if not required.issubset(paths):
            raise ValueError(
                'a trial needs its job config and trial config, lock, and result'
            )
        seen: set[str] = set()
        for entry in self.files:
            parts = _path_parts(entry.path)
            if not _selected_path(parts, self.trial_name):
                raise ValueError(
                    'manifest files must belong to the selected job and trial'
                )
            if any('/'.join(parts[:index]) in seen for index in range(1, len(parts))):
                raise ValueError("a manifest file cannot be another file's parent")
            if entry.path in required and entry.mode == '120000':
                raise ValueError('required trial records must be regular files')
            seen.add(entry.path)
        return self


@dataclass(frozen=True)
class MaterializedTrial:
    job: Path
    manifest: TrialManifest


def build_manifest(
    source_job_dir: Path,
    *,
    scope: str,
    run_id: str,
    artifact_id: str,
    trial_id: str,
    job_name: str,
    trial_name: str,
    code_revision: str | None,
    case_revision: str | None,
) -> TrialManifest:
    """Describe the selected trial's raw files without replacing a secret audit.

    The caller audits these files before publication. Workspace files retain
    their names and bytes, including lockfiles and database fixtures. Only the
    caches already excluded from workspace diffs are omitted.
    """
    try:
        return TrialManifest(
            scope=scope,
            run_id=run_id,
            artifact_id=artifact_id,
            trial_id=trial_id,
            job_name=job_name,
            trial_name=trial_name,
            code_revision=code_revision,
            case_revision=case_revision,
            files=tuple(
                entry for entry, _ in _selected_files(source_job_dir, trial_name)
            ),
        )
    except ValidationError as error:
        raise InputError(
            'The selected files do not form a complete trial manifest.'
        ) from error


# ── Repository ──


class _Repository(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)

    full_name: str
    private: bool
    ssh_url: str
    default_branch: str | None = None


class ArtifactRepository:
    """Read pinned cases and publish audited trials through `gh` and native Git auth."""

    def __init__(self, repository_slug: str) -> None:
        if not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository_slug
        ):
            raise InputError(
                'A private artifact repository must be named `owner/repository`.'
            )
        self.repository_slug = repository_slug

    def head(self) -> str:
        """Resolve private remote `main` to an immutable source revision."""
        repository = self._metadata()
        output = _git(
            Path.cwd(),
            ['ls-remote', '--heads', '--', repository.ssh_url, 'refs/heads/main'],
        ).stdout
        if not output:
            raise InputError('The private repository has no `main` source revision.')
        revision = output.decode().split()[0]
        _commit(revision)
        return revision

    def source(self, revision: str, destination: Path) -> Path:
        """Restore only `cases/` from an immutable commit into an empty directory."""
        _commit(revision)
        repository = self._metadata()
        with tempfile.TemporaryDirectory(prefix='bench-artifacts-') as temporary:
            clone = _clone(repository, Path(temporary))
            _git(clone, ['fetch', '--no-tags', 'origin', revision])
            entries = _tree_files(clone, revision, 'cases')
            if not entries or any(not path.startswith('cases/') for path in entries):
                raise InputError(
                    'The private source revision contains no `cases/` tree.'
                )
            _restore(destination, entries)
        return destination / 'cases'

    def publish_trial(
        self,
        run_id: str,
        artifact_id: str,
        source_job_dir: Path,
        manifest: TrialManifest,
    ) -> ArtifactRef:
        """Publish an audited immutable trial and confirm it exists on remote `main`.

        Repeated publication of the same files returns their original commit.
        A competing writer is reconciled only by adding this trial's new path.
        A returned reference never names an unconfirmed local commit.
        """
        if (run_id, artifact_id) != (manifest.run_id, manifest.artifact_id):
            raise InputError('The publication identifiers do not match the manifest.')
        selected = list(_selected_files(source_job_dir, manifest.trial_name))
        if tuple(entry for entry, _ in selected) != manifest.files:
            raise InputError(
                'The selected trial files differ from their audited manifest.'
            )
        content = manifest.content()
        digest = sha256(content).hexdigest()
        repository = self._metadata()
        with tempfile.TemporaryDirectory(prefix='bench-artifacts-') as temporary:
            clone = _clone(repository, Path(temporary))
            while True:
                parent = _remote_head(clone)
                if parent is not None:
                    _git(clone, ['fetch', '--no-tags', 'origin', parent])
                    existing = _tree_files(clone, parent, manifest.path)
                    if existing:
                        expected = {
                            f'{manifest.path}/{entry.path}': (entry.mode, value)
                            for entry, value in selected
                        }
                        expected[f'{manifest.path}/manifest.json'] = ('100644', content)
                        if existing != expected:
                            raise InputError(
                                'The trial path contains different immutable artifacts.'
                            )
                        commit = (
                            _git(
                                clone,
                                [
                                    'log',
                                    '-1',
                                    '--format=%H',
                                    parent,
                                    '--',
                                    manifest.path,
                                ],
                            )
                            .stdout.decode()
                            .strip()
                        )
                        return ArtifactRef(
                            commit_sha=commit,
                            path=manifest.path,
                            manifest_digest=digest,
                        )
                _git(
                    clone,
                    [
                        'sparse-checkout',
                        'set',
                        '--cone',
                        '--skip-checks',
                        manifest.path,
                    ],
                )
                if parent is not None:
                    _git(clone, ['switch', '--detach', parent])
                else:
                    _git(clone, ['symbolic-ref', 'HEAD', 'refs/heads/main'])
                ancestor = clone
                for component in manifest.path.split('/')[:-1]:
                    ancestor /= component
                    if ancestor.is_symlink() or (
                        ancestor.exists() and not ancestor.is_dir()
                    ):
                        raise InputError(
                            'The trial path cannot traverse a tracked file or symlink.'
                        )
                trial_files: dict[str, tuple[GitMode, bytes]] = {
                    entry.path: (entry.mode, value) for entry, value in selected
                }
                trial_files['manifest.json'] = ('100644', content)
                _restore(clone / manifest.path, trial_files)
                staged_paths = [f'{manifest.path}/{path}' for path in trial_files]
                _git(
                    clone,
                    [
                        '--literal-pathspecs',
                        'add',
                        '--force',
                        '--pathspec-from-file=-',
                        '--pathspec-file-nul',
                    ],
                    content=b''.join(
                        os.fsencode(path) + b'\0' for path in staged_paths
                    ),
                )
                _git(clone, ['commit', '-m', 'feat(artifacts): record immutable trial'])
                commit = _git(clone, ['rev-parse', 'HEAD']).stdout.decode().strip()
                expected = {
                    f'{manifest.path}/{path}': value
                    for path, value in trial_files.items()
                }
                if _tree_files(clone, commit, manifest.path) != expected:
                    raise InputError(
                        'The artifact commit changed the audited trial files.'
                    )
                changed = _git(
                    clone,
                    [
                        'diff-tree',
                        '--root',
                        '--no-commit-id',
                        '--name-only',
                        '-r',
                        '-z',
                        commit,
                    ],
                ).stdout
                if {os.fsdecode(path) for path in changed.split(b'\0') if path} != set(
                    expected
                ):
                    raise InputError(
                        'The artifact commit changed paths outside this trial.'
                    )
                self._metadata()
                pushed = _git(
                    clone, ['push', 'origin', f'{commit}:refs/heads/main'], check=False
                )
                if pushed.returncode:
                    if _remote_head(clone) != parent:
                        continue
                    raise InputError(
                        'The immutable trial could not be pushed to the repository.'
                    )
                self._confirm(clone, commit, manifest, content)
                return ArtifactRef(
                    commit_sha=commit, path=manifest.path, manifest_digest=digest
                )

    def materialize(
        self, reference: ArtifactRef, destination: Path
    ) -> MaterializedTrial:
        """Restore verified raw job files into an empty private directory.

        No JSON paths are rewritten. The execution layer prepares Harbor's
        concrete task and source-job locations after this raw restoration.
        """
        repository = self._metadata()
        with tempfile.TemporaryDirectory(prefix='bench-artifacts-') as temporary:
            clone = _clone(repository, Path(temporary))
            _git(clone, ['fetch', '--no-tags', 'origin', reference.commit_sha])
            files = _tree_files(clone, reference.commit_sha, reference.path)
            manifest_path = f'{reference.path}/manifest.json'
            if manifest_path not in files or files[manifest_path][0] != '100644':
                raise InputError('The artifact reference contains no regular manifest.')
            content = files.pop(manifest_path)[1]
            if sha256(content).hexdigest() != reference.manifest_digest:
                raise InputError(
                    'The artifact manifest differs from its recorded digest.'
                )
            try:
                manifest = TrialManifest.model_validate_json(content)
            except ValidationError as error:
                raise InputError('The artifact manifest is invalid.') from error
            if manifest.path != reference.path:
                raise InputError('The artifact manifest names another trial path.')
            expected = {
                f'{reference.path}/{entry.path}': entry for entry in manifest.files
            }
            if files.keys() != expected.keys():
                raise InputError(
                    'The artifact tree differs from its manifest file list.'
                )
            restored: dict[str, tuple[GitMode, bytes]] = {}
            for path, (mode, value) in files.items():
                entry = expected[path]
                if mode != entry.mode or sha256(value).hexdigest() != entry.sha256:
                    raise InputError(
                        'An artifact file differs from its recorded mode or digest.'
                    )
                restored[entry.path] = (mode, value)
            _restore(destination, restored)
        return MaterializedTrial(job=destination / 'job', manifest=manifest)

    def _metadata(self) -> _Repository:
        response = _command(['gh', 'api', f'repos/{self.repository_slug}'])
        try:
            repository = _Repository.model_validate_json(response.stdout)
        except ValidationError as error:
            raise InputError(
                'GitHub returned unreadable artifact repository metadata.'
            ) from error
        if (
            repository.full_name.casefold() != self.repository_slug.casefold()
            or not repository.private
        ):
            raise InputError(
                'Artifacts require the configured private GitHub repository.'
            )
        if repository.default_branch not in (None, 'main'):
            raise InputError(
                'The private artifact repository must use `main` as its default branch.'
            )
        return repository

    def _confirm(
        self, clone: Path, commit: str, manifest: TrialManifest, content: bytes
    ) -> None:
        head = _remote_head(clone)
        if head is None:
            raise InputError('The published trial is missing from remote `main`.')
        _git(clone, ['fetch', '--no-tags', 'origin', head])
        ancestor = _git(
            clone, ['merge-base', '--is-ancestor', commit, head], check=False
        )
        if ancestor.returncode:
            raise InputError('The published trial is not retained by remote `main`.')
        confirmed = _git(
            clone, ['show', f'{commit}:{manifest.path}/manifest.json']
        ).stdout
        if confirmed != content:
            raise InputError(
                'The remote trial manifest differs from the published manifest.'
            )


# ── File Selection ──


def _path_parts(path: str) -> tuple[str, ...]:
    parts = tuple(path.split('/'))
    if any(
        part in ('', '.', '..')
        or '\0' in part
        or part.casefold() == '.git'
        or part in LEFT_OUT
        for part in parts
    ):
        raise ValueError(
            'artifact paths must be relative and exclude Git internals and caches'
        )
    return parts


def _selected_path(parts: tuple[str, ...], trial_name: str) -> bool:
    if len(parts) == 2:
        return parts[0] == 'job' and parts[1] in JOB_FILES
    return (
        len(parts) >= 3
        and parts[:2] == ('job', trial_name)
        and (
            (len(parts) == 3 and parts[2] in TRIAL_FILES)
            or (len(parts) > 3 and parts[2] in TRIAL_DIRECTORIES)
        )
    )


def _selected_files(root: Path, trial_name: str) -> Iterator[tuple[FileEntry, bytes]]:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', trial_name):
        raise InputError('A trial name must be a single path component.')
    if root.is_symlink() or not root.is_dir():
        raise InputError('The source job must be a regular directory.')
    paths = [
        root / name
        for name in JOB_FILES
        if (root / name).exists() or (root / name).is_symlink()
    ]
    trial = root / trial_name
    if trial.is_symlink() or not trial.is_dir():
        raise InputError('The selected trial must be a regular directory.')
    paths += [
        trial / name
        for name in TRIAL_FILES
        if (trial / name).exists() or (trial / name).is_symlink()
    ]
    for name in TRIAL_DIRECTORIES:
        directory = trial / name
        if directory.is_symlink():
            raise InputError('A selected trial artifact directory cannot be a symlink.')
        if directory.exists():
            paths.extend(_walk(directory))
    for path in sorted(paths):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            git_mode: GitMode = '120000'
            content = os.fsencode(os.readlink(path))
        elif stat.S_ISREG(mode):
            git_mode = '100755' if mode & stat.S_IXUSR else '100644'
            content = path.read_bytes()
        else:
            raise InputError('Trial artifacts must be regular files or symlinks.')
        yield (
            FileEntry(
                path=f'job/{path.relative_to(root).as_posix()}',
                mode=git_mode,
                sha256=sha256(content).hexdigest(),
            ),
            content,
        )


def _walk(root: Path) -> Iterator[Path]:
    for path in sorted(root.iterdir()):
        if path.name in LEFT_OUT or path.name.casefold() == '.git':
            continue
        if path.is_symlink() or not path.is_dir():
            yield path
        else:
            yield from _walk(path)


# ── Git Objects ──


@dataclass(frozen=True)
class _TreeEntry:
    path: str
    mode: GitMode
    object_id: str


def _command(
    arguments: Sequence[str],
    *,
    content: bytes | None = None,
    check: bool = True,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            arguments, input=content, capture_output=True, env=environment
        )
    except FileNotFoundError as error:
        raise InputError('Private artifacts require `git` and `gh` on PATH.') from error
    if check and result.returncode:
        raise InputError(f'Private artifact `{arguments[0]}` command failed.')
    return result


def _git(
    root: Path,
    arguments: Sequence[str],
    *,
    content: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    variables = (
        _command(['git', 'rev-parse', '--local-env-vars']).stdout.decode().splitlines()
    )
    environment = {
        name: value for name, value in os.environ.items() if name not in variables
    }
    return _command(
        ['git', '-C', str(root), *arguments],
        content=content,
        check=check,
        environment=environment,
    )


def _clone(repository: _Repository, root: Path) -> Path:
    clone = root / 'repository'
    _git(
        root,
        [
            'clone',
            '--filter=blob:none',
            '--no-checkout',
            '--',
            repository.ssh_url,
            str(clone),
        ],
    )
    information = clone / '.git' / 'info'
    information.mkdir(exist_ok=True)
    (information / 'attributes').write_text(
        '* -text -filter -ident -working-tree-encoding\n'
    )
    return clone


def _remote_head(root: Path) -> str | None:
    output = _git(root, ['ls-remote', '--heads', 'origin', 'refs/heads/main']).stdout
    return output.decode().split()[0] if output else None


def _tree_files(root: Path, commit: str, path: str) -> dict[str, tuple[GitMode, bytes]]:
    records = _git(root, ['ls-tree', '-r', '-z', commit, '--', path]).stdout
    entries: list[_TreeEntry] = []
    for record in records.split(b'\0'):
        if not record:
            continue
        header, raw_path = record.split(b'\t', 1)
        mode, kind, object_id = header.decode().split()
        if kind != 'blob' or mode not in GIT_MODES:
            raise InputError(
                'Private artifacts cannot contain Git submodules or unsupported modes.'
            )
        name = raw_path.decode()
        try:
            _path_parts(name)
        except ValueError as error:
            raise InputError(
                'The private repository contains an unsafe artifact path.'
            ) from error
        entries.append(_TreeEntry(name, cast(GitMode, mode), object_id))
    if not entries:
        return {}
    objects = _git(
        root,
        ['cat-file', '--batch'],
        content=''.join(f'{entry.object_id}\n' for entry in entries).encode(),
    ).stdout
    stream = io.BytesIO(objects)
    files: dict[str, tuple[GitMode, bytes]] = {}
    for entry in entries:
        _, kind, size = stream.readline().decode().split()
        assert kind == 'blob'
        content = stream.read(int(size))
        assert stream.read(1) == b'\n'
        files[entry.path] = (entry.mode, content)
    return files


def _restore(destination: Path, files: dict[str, tuple[GitMode, bytes]]) -> None:
    if destination.is_symlink() or (
        destination.exists()
        and (not destination.is_dir() or any(destination.iterdir()))
    ):
        raise InputError('Artifact restoration needs an empty destination directory.')
    paths = set(files)
    for relative in paths:
        parts = _path_parts(relative)
        if any('/'.join(parts[:index]) in paths for index in range(1, len(parts))):
            raise InputError("An artifact file cannot be another file's parent.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix='.bench-artifacts-', dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / 'contents'
        staged.mkdir(mode=0o700)
        directories = {staged}
        for relative, (mode, content) in sorted(files.items()):
            parts = _path_parts(relative)
            directory = staged
            for component in parts[:-1]:
                child = directory / component
                if child not in directories:
                    if child.is_symlink():
                        raise InputError(
                            'An artifact directory cannot traverse a symlink.'
                        )
                    if child.exists():
                        raise InputError(
                            'Artifact paths collide on the destination filesystem.'
                        )
                    child.mkdir()
                    directories.add(child)
                directory = child
            path = directory / parts[-1]
            if path.is_symlink() or path.exists():
                raise InputError(
                    'Artifact paths collide on the destination filesystem.'
                )
            if mode == '120000':
                path.symlink_to(os.fsdecode(content))
            else:
                with path.open('xb') as file:
                    file.write(content)
                path.chmod(0o755 if mode == '100755' else 0o644)
        staged.replace(destination)


def _commit(revision: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise InputError('Private sources require a full immutable Git commit SHA.')
