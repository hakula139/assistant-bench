import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import semver
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.job.config import SourceJobConfig
from harbor.models.task.config import VerifierEnvironmentMode
from harbor.models.task.task import Task
from harbor.models.trial.result import TrialResult
from pydantic import ValidationError

from assistant_bench import collection, jobs
from assistant_bench.artifacts import (
    ArtifactRef,
    ArtifactRepository,
    FileEntry,
    TrialManifest,
    build_manifest,
)
from assistant_bench.families import Family, load_family
from assistant_bench.messages import InputError
from support import FIXTURES, finish_generation, make_system, write_votes_family


pytestmark = pytest.mark.usefixtures('export')

SLUG = 'example/private-data'
CODE_REVISION = '1' * 40


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ['git', *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@dataclass(frozen=True)
class Repository:
    client: ArtifactRepository
    remote: Path
    seed: Path
    metadata: Path
    revision: str


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repository:
    for name in (
        'GIT_DIR',
        'GIT_WORK_TREE',
        'GIT_INDEX_FILE',
        'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES',
        'GIT_CONFIG_COUNT',
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    for name in ('GIT_AUTHOR_NAME', 'GIT_COMMITTER_NAME'):
        monkeypatch.setenv(name, 'Test')
    for name in ('GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_EMAIL'):
        monkeypatch.setenv(name, 'test@example.com')
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--quiet', '--bare', '--initial-branch=main', str(remote))
    seed = tmp_path / 'seed'
    seed.mkdir()
    git(seed, 'init', '--quiet', '--initial-branch=main')
    cases = seed / 'cases'
    cases.mkdir()
    (cases / 'sample.toml').write_bytes(b'cases = []\r\n')
    (seed / 'unrelated.txt').write_text('Not a case source.\n')
    git(seed, 'add', '.')
    git(seed, 'commit', '--quiet', '-m', 'Add synthetic sources')
    git(seed, 'remote', 'add', 'origin', str(remote))
    git(seed, 'push', '--quiet', 'origin', 'main')
    revision = git(seed, 'rev-parse', 'HEAD')
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(
        json.dumps(
            {
                'full_name': SLUG,
                'private': True,
                'ssh_url': str(remote),
                'default_branch': 'main',
            }
        )
    )
    binary = tmp_path / 'bin'
    binary.mkdir()
    stub = binary / 'gh'
    stub.write_text(
        f'#!{sys.executable}\n' + (FIXTURES / 'artifacts' / 'gh.py').read_text()
    )
    stub.chmod(0o755)
    monkeypatch.setenv('PATH', f'{binary}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setenv('TEST_GH_METADATA', str(metadata))
    monkeypatch.delenv('TEST_GH_STATUS', raising=False)
    return Repository(ArtifactRepository(SLUG), remote, seed, metadata, revision)


@dataclass(frozen=True)
class Trial:
    family: Family
    job: Path
    task: Path
    result: TrialResult
    manifest: TrialManifest


@pytest.fixture
def trial(
    repository: Repository, families_dir: Path, data_dir: Path, tmp_path: Path
) -> Trial:
    write_votes_family(families_dir, ['acme-large'], {'brief': 'reply'})
    family = load_family('f')
    task = data_dir / 'tasks' / 'f' / 'brief'
    prepared = Task(task, disable_verification=True)
    prepared.config.verifier.environment_mode = VerifierEnvironmentMode.SEPARATE
    (task / 'task.toml').write_text(prepared.config.model_dump_toml())
    config = jobs.job(
        family, jobs.family_systems(family, [make_system()]), [task]
    ).model_copy(update={'jobs_dir': tmp_path / 'source-jobs', 'job_name': 'job-a'})
    finish_generation(config, 'First line.\r\nSecond line.\n')
    job = config.jobs_dir / config.job_name
    (result_path,) = job.glob('*/result.json')
    result = TrialResult.model_validate_json(result_path.read_text())
    trial_dir = result_path.parent
    (trial_dir / 'config.json').write_text(result.config.model_dump_json())
    (trial_dir / 'trial.log').write_text('Trial finished.\n')
    workspace = trial_dir / 'artifacts' / 'app'
    workspace.mkdir(parents=True)
    (workspace / 'answer.txt').write_bytes(b'Answer.\r\n')
    executable = workspace / 'run.sh'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    (workspace / 'link').symlink_to('answer.txt')
    (workspace / 'Cargo.lock').write_text('version = 4\n')
    (workspace / 'yarn.lock').write_text('# yarn lockfile v1\n')
    (workspace / '.env.example').write_text('COLOR=blue\n')
    (workspace / 'credentials.py').write_text("LABEL = 'example'\n")
    vendor = workspace / 'vendor'
    vendor.mkdir()
    git(vendor, 'init', '--quiet', '--initial-branch=main')
    (vendor / 'module.py').write_text("LABEL = 'nested repository contents'\n")
    git(vendor, 'add', '.')
    git(vendor, 'commit', '--quiet', '-m', 'Add synthetic nested source')
    with sqlite3.connect(workspace / 'fixture.sqlite') as database:
        database.execute('CREATE TABLE sample (value TEXT)')
        database.execute("INSERT INTO sample VALUES ('example')")
    for excluded in ('.git', '__pycache__'):
        directory = workspace / excluded
        directory.mkdir()
        (directory / 'generated').write_text('Generated state.\n')
    manifest = build_manifest(
        job,
        scope='sample',
        run_id='run-a',
        claim_id='claim-a',
        trial_id=str(result.id),
        job_name=job.name,
        trial_name=result.trial_name,
        code_revision=CODE_REVISION,
        case_revision=repository.revision,
    )
    return Trial(family, job, task, result, manifest)


def publish(repository: Repository, trial: Trial) -> ArtifactRef:
    return repository.client.publish_trial(
        'run-a', 'claim-a', trial.job, trial.manifest
    )


def snapshot(trial: Trial) -> TrialManifest:
    return build_manifest(
        trial.job,
        scope=trial.manifest.scope,
        run_id=trial.manifest.run_id,
        claim_id=trial.manifest.claim_id,
        trial_id=trial.manifest.trial_id,
        job_name=trial.manifest.job_name,
        trial_name=trial.manifest.trial_name,
        code_revision=trial.manifest.code_revision,
        case_revision=trial.manifest.case_revision,
    )


def contents(path: Path) -> bytes:
    return os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()


class TestFileEntry:
    @pytest.mark.parametrize(
        'path',
        [
            '/outside.txt',
            '../outside.txt',
            'job/../outside.txt',
            'job/.git/config',
            'job/trial/artifacts/app/__pycache__/cache.pyc',
        ],
        ids=['absolute', 'parent', 'interior-parent', 'git', 'cache'],
    )
    def test_refuses_paths_outside_the_retained_tree(self, path: str) -> None:
        with pytest.raises(ValidationError):
            FileEntry(path=path, mode='100644', sha256='a' * 64)

    def test_refuses_a_gitlink_instead_of_silently_omitting_its_contents(self) -> None:
        with pytest.raises(ValidationError):
            FileEntry.model_validate(
                {
                    'path': 'job/trial/artifacts/app/vendor',
                    'mode': '160000',
                    'sha256': 'a' * 64,
                }
            )


class TestTrialManifest:
    @pytest.mark.parametrize('root', ['agent', 'verifier', 'artifacts'])
    @pytest.mark.parametrize('mode', ['100644', '120000'])
    def test_artifact_roots_must_be_directories(
        self, trial: Trial, root: str, mode: str
    ) -> None:
        prefix = f'job/{trial.result.trial_name}/{root}'
        files = [
            item
            for item in trial.manifest.files
            if not item.path.startswith(f'{prefix}/')
        ]
        files.append(
            FileEntry.model_validate({'path': prefix, 'mode': mode, 'sha256': 'a' * 64})
        )

        with pytest.raises(ValidationError):
            TrialManifest.model_validate(
                {
                    **trial.manifest.model_dump(),
                    'files': tuple(sorted(files, key=lambda item: item.path)),
                }
            )

    def test_refuses_a_file_below_a_symlink_parent(self, trial: Trial) -> None:
        parent = f'job/{trial.result.trial_name}/artifacts/app/link'
        descendant = FileEntry(
            path=f'{parent}/outside.txt', mode='100644', sha256='a' * 64
        )
        files = sorted([*trial.manifest.files, descendant], key=lambda item: item.path)

        with pytest.raises(ValidationError):
            TrialManifest.model_validate(
                {**trial.manifest.model_dump(), 'files': tuple(files)}
            )


class TestBuildManifest:
    @pytest.mark.parametrize(
        'record', ['job', 'config.json', 'lock.json', 'result.json']
    )
    def test_required_harbor_records_cannot_point_outside_the_job(
        self, trial: Trial, tmp_path: Path, record: str
    ) -> None:
        path = (
            trial.job / 'config.json'
            if record == 'job'
            else trial.job / trial.result.trial_name / record
        )
        outside = tmp_path / 'outside.json'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)

        with pytest.raises(InputError):
            snapshot(trial)

        assert outside.read_bytes() == path.read_bytes()

    def test_selected_artifact_directory_cannot_be_a_symlink(
        self, trial: Trial, tmp_path: Path
    ) -> None:
        directory = trial.job / trial.result.trial_name / 'artifacts'
        outside = tmp_path / 'outside'
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)

        with pytest.raises(InputError):
            snapshot(trial)

        assert (outside / 'app' / 'answer.txt').read_bytes() == b'Answer.\r\n'


class TestArtifactRepository:
    def test_inherited_git_locations_cannot_redirect_private_storage_operations(
        self,
        repository: Repository,
        trial: Trial,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with monkeypatch.context() as environment:
            for name in ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE']:
                environment.setenv(name, str(tmp_path / 'foreign' / name))

            reference = publish(repository, trial)
            restored = repository.client.materialize(reference, tmp_path / 'restored')
            source = repository.client.source(repository.revision, tmp_path / 'sources')

        assert (restored / 'config.json').read_bytes() == (
            trial.job / 'config.json'
        ).read_bytes()
        assert (source / 'sample.toml').read_bytes() == b'cases = []\r\n'
        assert not (tmp_path / 'foreign').exists()

    class TestSource:
        def test_materializes_only_case_sources_at_the_requested_revision(
            self, repository: Repository, tmp_path: Path
        ) -> None:
            (repository.seed / 'cases' / 'sample.toml').write_text('cases = [1]\n')
            git(repository.seed, 'add', '.')
            git(repository.seed, 'commit', '--quiet', '-m', 'Change synthetic source')
            git(repository.seed, 'push', '--quiet', 'origin', 'main')

            source = repository.client.source(repository.revision, tmp_path / 'client')

            assert (source / 'sample.toml').read_bytes() == b'cases = []\r\n'
            assert not (tmp_path / 'client' / 'unrelated.txt').exists()
            assert source.is_relative_to(tmp_path / 'client')

    class TestPublishTrial:
        @pytest.mark.parametrize(
            'failure',
            ['public', 'unreadable', 'wrong-repository', 'coerced-visibility'],
        )
        def test_checks_private_visibility_before_pushing_any_artifact(
            self,
            repository: Repository,
            trial: Trial,
            monkeypatch: pytest.MonkeyPatch,
            failure: str,
        ) -> None:
            before = git(repository.remote, 'rev-parse', 'main')
            metadata: dict[str, Any] = json.loads(repository.metadata.read_text())
            if failure == 'public':
                metadata['private'] = False
                repository.metadata.write_text(json.dumps(metadata))
            elif failure == 'unreadable':
                monkeypatch.setenv('TEST_GH_STATUS', '1')
            else:
                if failure == 'wrong-repository':
                    metadata['full_name'] = 'example/another-data'
                else:
                    metadata['private'] = 'true'
                repository.metadata.write_text(json.dumps(metadata))

            with pytest.raises(InputError):
                publish(repository, trial)

            assert git(repository.remote, 'rev-parse', 'main') == before

        def test_returns_only_a_remotely_readable_reference_and_is_idempotent(
            self, repository: Repository, trial: Trial
        ) -> None:
            reference = publish(repository, trial)

            repeated = publish(repository, trial)

            assert repeated == reference
            assert git(repository.remote, 'rev-parse', 'main') == reference.commit_sha
            manifest = (
                git(
                    repository.remote,
                    'show',
                    f'{reference.commit_sha}:{reference.path}/manifest.json',
                ).encode()
                + b'\n'
            )
            assert hashlib.sha256(manifest).hexdigest() == reference.manifest_digest
            assert json.loads(manifest) == trial.manifest.model_dump(mode='json')
            tree = git(repository.remote, 'ls-tree', '-r', reference.commit_sha)
            assert '160000' not in {line.split()[0] for line in tree.splitlines()}
            assert '/.git/' not in tree
            assert '/__pycache__/' not in tree

        def test_does_not_return_a_durable_reference_when_the_push_is_rejected(
            self, repository: Repository, trial: Trial
        ) -> None:
            before = git(repository.remote, 'rev-parse', 'main')
            hook = repository.remote / 'hooks' / 'pre-receive'
            hook.write_text('#!/bin/sh\nexit 1\n')
            hook.chmod(0o755)

            with pytest.raises(InputError):
                publish(repository, trial)

            assert git(repository.remote, 'rev-parse', 'main') == before

        @pytest.mark.parametrize('change', ['content', 'outside'])
        def test_commit_hooks_cannot_publish_changes_beyond_the_audited_tree(
            self,
            repository: Repository,
            trial: Trial,
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
            change: str,
        ) -> None:
            before = git(repository.remote, 'rev-parse', 'main')
            template = tmp_path / 'template'
            hooks = template / 'hooks'
            hooks.mkdir(parents=True)
            hook = hooks / 'pre-commit'
            hook.write_text(
                f'#!{sys.executable}\n'
                + (FIXTURES / 'artifacts' / 'commit.py').read_text()
            )
            hook.chmod(0o755)
            monkeypatch.setenv('GIT_TEMPLATE_DIR', str(template))
            monkeypatch.setenv('TEST_ARTIFACT_HOOK', change)

            with pytest.raises(InputError):
                publish(repository, trial)

            assert git(repository.remote, 'rev-parse', 'main') == before

        def test_refuses_overwriting_a_claim_with_different_content(
            self, repository: Repository, trial: Trial
        ) -> None:
            reference = publish(repository, trial)
            result = trial.job / trial.result.trial_name / 'agent' / 'output.txt'
            result.write_text('Changed answer.\n')
            changed = build_manifest(
                trial.job,
                scope='sample',
                run_id='run-a',
                claim_id='claim-a',
                trial_id=str(trial.result.id),
                job_name=trial.job.name,
                trial_name=trial.result.trial_name,
                code_revision=CODE_REVISION,
                case_revision=repository.revision,
            )

            with pytest.raises(InputError):
                repository.client.publish_trial('run-a', 'claim-a', trial.job, changed)

            assert git(repository.remote, 'rev-parse', 'main') == reference.commit_sha

        def test_rechecks_selected_files_after_the_manifest_was_built(
            self, repository: Repository, trial: Trial
        ) -> None:
            before = git(repository.remote, 'rev-parse', 'main')
            (trial.job / trial.result.trial_name / 'agent' / 'output.txt').write_text(
                'Changed after audit.\n'
            )

            with pytest.raises(InputError):
                publish(repository, trial)

            assert git(repository.remote, 'rev-parse', 'main') == before

        def test_concurrent_pushes_preserve_both_immutable_trials(
            self,
            repository: Repository,
            trial: Trial,
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            barrier = tmp_path / 'pushes'
            barrier.mkdir()
            monkeypatch.setenv('TEST_PUSH_BARRIER', str(barrier))
            hook = repository.remote / 'hooks' / 'pre-receive'
            hook.write_text(
                f'#!{sys.executable}\n'
                + (FIXTURES / 'artifacts' / 'receive.py').read_text()
            )
            hook.chmod(0o755)
            second = TrialManifest.model_validate(
                {
                    **trial.manifest.model_dump(),
                    'run_id': 'run-b',
                    'claim_id': 'claim-b',
                }
            )

            def push(manifest: TrialManifest) -> ArtifactRef:
                return ArtifactRepository(SLUG).publish_trial(
                    manifest.run_id, manifest.claim_id, trial.job, manifest
                )

            with ThreadPoolExecutor(max_workers=2) as writers:
                references = list(writers.map(push, [trial.manifest, second]))

            for reference in references:
                git(
                    repository.remote,
                    'merge-base',
                    '--is-ancestor',
                    reference.commit_sha,
                    'main',
                )
                restored = repository.client.materialize(
                    reference, tmp_path / reference.path.split('/')[1]
                )
                assert (
                    restored / trial.result.trial_name / 'agent' / 'output.txt'
                ).read_bytes() == (
                    trial.job / trial.result.trial_name / 'agent' / 'output.txt'
                ).read_bytes()
            assert git(repository.remote, 'show', 'main:unrelated.txt') == (
                'Not a case source.'
            )

    class TestMaterialize:
        @pytest.mark.parametrize(
            'failure', ['bytes', 'mode', 'manifest', 'extra', 'missing']
        )
        def test_refuses_remote_trees_that_differ_from_the_recorded_manifest(
            self,
            repository: Repository,
            trial: Trial,
            tmp_path: Path,
            failure: str,
        ) -> None:
            reference = publish(repository, trial)
            git(repository.seed, 'pull', '--quiet', '--ff-only', 'origin', 'main')
            root = repository.seed / reference.path
            path = root / 'job' / trial.result.trial_name / 'agent' / 'output.txt'
            if failure == 'bytes':
                path.write_text('Changed remote bytes.\n')
            elif failure == 'mode':
                path.chmod(0o755)
            elif failure == 'manifest':
                path = root / 'manifest.json'
                path.write_bytes(path.read_bytes() + b'\n')
            elif failure == 'extra':
                (root / 'unlisted.txt').write_text('Not in the manifest.\n')
            else:
                (root / 'job' / trial.result.trial_name / 'lock.json').unlink()
            git(repository.seed, 'add', '.')
            git(repository.seed, 'commit', '--quiet', '-m', 'Change synthetic tree')
            git(repository.seed, 'push', '--quiet', 'origin', 'main')
            changed = reference.model_copy(
                update={'commit_sha': git(repository.seed, 'rev-parse', 'HEAD')}
            )

            with pytest.raises(InputError):
                repository.client.materialize(changed, tmp_path / 'restored')

            assert not (tmp_path / 'restored').exists()

        def test_external_symlinks_preserve_targets_without_reading_or_writing_them(
            self, repository: Repository, trial: Trial, tmp_path: Path
        ) -> None:
            outside = tmp_path / 'outside.txt'
            outside.write_bytes(b'Private sentinel, never archive this file.\n')
            workspace = trial.job / trial.result.trial_name / 'artifacts' / 'app'
            (workspace / 'external').symlink_to(outside)
            manifest = snapshot(trial)
            reference = repository.client.publish_trial(
                manifest.run_id, manifest.claim_id, trial.job, manifest
            )

            restored = repository.client.materialize(reference, tmp_path / 'restored')

            link = restored / trial.result.trial_name / 'artifacts' / 'app' / 'external'
            assert link.is_symlink()
            assert os.readlink(link) == str(outside)
            assert (
                outside.read_bytes() == b'Private sentinel, never archive this file.\n'
            )
            archived = subprocess.run(
                ['git', 'archive', reference.commit_sha],
                cwd=repository.remote,
                check=True,
                capture_output=True,
            ).stdout
            assert b'Private sentinel' not in archived

        def test_restores_original_bytes_modes_and_links_after_the_client_is_gone(
            self, repository: Repository, trial: Trial, tmp_path: Path
        ) -> None:
            reference = publish(repository, trial)
            originals = {
                item.path: contents(trial.job / Path(item.path).relative_to('job'))
                for item in trial.manifest.files
            }
            shutil.rmtree(trial.job.parent)

            restored = ArtifactRepository(SLUG).materialize(
                reference, tmp_path / 'fresh'
            )

            for item in trial.manifest.files:
                path = restored / Path(item.path).relative_to('job')
                assert contents(path) == originals[item.path], item.path
                assert path.is_symlink() == (item.mode == '120000'), item.path
                if not path.is_symlink():
                    assert bool(path.stat().st_mode & stat.S_IXUSR) == (
                        item.mode == '100755'
                    ), item.path
            workspace = restored / trial.result.trial_name / 'artifacts' / 'app'
            for name in [
                'Cargo.lock',
                'yarn.lock',
                'fixture.sqlite',
                '.env.example',
                'credentials.py',
            ]:
                assert (workspace / name).exists(), name
            assert (workspace / 'vendor' / 'module.py').read_text() == (
                "LABEL = 'nested repository contents'\n"
            )
            assert not (workspace / 'vendor' / '.git').exists()
            assert not (workspace / '.git').exists()
            assert not (workspace / '__pycache__').exists()

        @pytest.mark.parametrize(
            ('minor_bump', 'expected'),
            [(False, DiffAction.REUSE), (True, DiffAction.REGRADE)],
            ids=['reuse', 'regrade'],
        )
        def test_retains_the_files_harbor_needs_for_reuse_and_regrade(
            self,
            repository: Repository,
            trial: Trial,
            tmp_path: Path,
            minor_bump: bool,
            expected: DiffAction,
        ) -> None:
            original = collection.read_trials(trial.family, trial.job)
            reference = publish(repository, trial)
            shutil.rmtree(trial.job.parent)
            restored = ArtifactRepository(SLUG).materialize(
                reference, tmp_path / 'fresh'
            )
            prepared = tmp_path / 'prepared' / trial.manifest.job_name
            shutil.copytree(restored, prepared, symlinks=True)
            if minor_bump:
                task = Task(trial.task, disable_verification=True)
                assert task.config.task is not None
                assert task.config.task.version is not None
                task.config.task.version = str(
                    semver.Version.parse(task.config.task.version).bump_minor()
                )
                (trial.task / 'task.toml').write_text(task.config.model_dump_toml())
            current = jobs.job(
                trial.family,
                jobs.family_systems(trial.family, [make_system()]),
                [trial.task],
            )
            plan = asyncio.run(JobPlan.from_config(current))
            source = SourceJobConfig(action='diff', type='local', path=prepared)

            diff = compare_job_trials(plan, load_source_trials([(source, prepared)]))

            assert [(item.task_name, item.action) for item in diff.trials] == [
                ('f/brief', expected)
            ]
            assert collection.read_trials(trial.family, prepared) == original
