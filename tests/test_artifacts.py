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
from contextlib import closing
from dataclasses import dataclass, replace
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
    MaterializedTrial,
    TrialManifest,
    audit_trial,
    build_manifest,
    snapshot_trial,
)
from assistant_bench.families import Family, load_family
from assistant_bench.messages import InputError
from support import (
    FIXTURES,
    PrivateRepository,
    finish_generation,
    git,
    make_system,
    write_votes_family,
)


pytestmark = pytest.mark.usefixtures('export')

SLUG = 'example/private-data'
CODE_REVISION = '1' * 40


@pytest.fixture
def repository(private_repository: PrivateRepository) -> PrivateRepository:
    return private_repository


@dataclass(frozen=True)
class Trial:
    family: Family
    job: Path
    task: Path
    result: TrialResult
    manifest: TrialManifest


@pytest.fixture
def trial(
    repository: PrivateRepository, families_dir: Path, data_dir: Path, tmp_path: Path
) -> Trial:
    write_votes_family(families_dir, ['acme-large'], {'brief': 'reply'})
    family = load_family('f')
    task = data_dir / 'tasks' / 'f' / 'brief'
    prepared = Task(task, disable_verification=True)
    prepared.config.verifier.environment_mode = VerifierEnvironmentMode.SEPARATE
    (task / 'task.toml').write_text(prepared.config.model_dump_toml())
    config = jobs.job(
        family,
        jobs.family_systems(family, [make_system()]),
        [task],
        root=tmp_path,
        sources=[],
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
    with closing(sqlite3.connect(workspace / 'fixture.sqlite')) as database, database:
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
        artifact_id='artifact-a',
        trial_id=str(result.id),
        job_name=job.name,
        trial_name=result.trial_name,
        code_revision=CODE_REVISION,
        case_revision=repository.revision,
    )
    return Trial(family, job, task, result, manifest)


def publish(repository: PrivateRepository, trial: Trial) -> ArtifactRef:
    (reference,) = repository.client.publish_trials(
        [MaterializedTrial(trial.job, trial.manifest)]
    )
    return reference


def snapshot(trial: Trial) -> TrialManifest:
    return build_manifest(
        trial.job,
        scope=trial.manifest.scope,
        run_id=trial.manifest.run_id,
        artifact_id=trial.manifest.artifact_id,
        trial_id=trial.manifest.trial_id,
        job_name=trial.manifest.job_name,
        trial_name=trial.manifest.trial_name,
        code_revision=trial.manifest.code_revision,
        case_revision=trial.manifest.case_revision,
    )


def contents(path: Path) -> bytes:
    return os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()


class TestAuditTrial:
    def test_refuses_a_known_credential_in_an_empty_directory_name(
        self, tmp_path: Path
    ) -> None:
        marker = 'sensitive-marker-for-test'
        (tmp_path / 'artifacts' / marker).mkdir(parents=True)

        with pytest.raises(InputError, match='known credential'):
            audit_trial(tmp_path, {marker})


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
    @pytest.mark.parametrize(
        'directory',
        [
            '/outside',
            '../outside',
            'job/other/artifacts',
            'job/{trial}/artifacts/app/__pycache__',
            'job/{trial}/artifacts/app/answer.txt',
            'job/{trial}/artifacts/app/link/child',
            'job/{trial}/artifacts/app/LINK',
        ],
        ids=['absolute', 'parent', 'other-trial', 'cache', 'file', 'symlink', 'alias'],
    )
    def test_directories_cannot_escape_or_collide_with_retained_files(
        self, trial: Trial, directory: str
    ) -> None:
        directories = tuple(
            sorted(
                [
                    *trial.manifest.directories,
                    directory.format(trial=trial.result.trial_name),
                ]
            )
        )

        with pytest.raises(ValidationError):
            TrialManifest.model_validate(
                {**trial.manifest.model_dump(), 'directories': directories}
            )

    @pytest.mark.parametrize('failure', ['missing', 'repeated', 'unsorted'])
    def test_directories_are_required_unique_and_canonical(
        self, trial: Trial, failure: str
    ) -> None:
        payload = trial.manifest.model_dump()
        if failure == 'missing':
            del payload['directories']
        elif failure == 'repeated':
            payload['directories'] = tuple(
                sorted([*trial.manifest.directories, trial.manifest.directories[0]])
            )
        else:
            payload['directories'] = tuple(reversed(trial.manifest.directories))

        with pytest.raises(ValidationError):
            TrialManifest.model_validate(payload)

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
        repository: PrivateRepository,
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

        assert (restored.job / 'config.json').read_bytes() == (
            trial.job / 'config.json'
        ).read_bytes()
        assert (source / 'sample.toml').read_bytes() == b'cases = []\r\n'
        assert not (tmp_path / 'foreign').exists()

    class TestHead:
        def test_reads_the_current_remote_main_without_changing_pinned_sources(
            self, repository: PrivateRepository, tmp_path: Path
        ) -> None:
            pinned = repository.client.head()
            (repository.seed / 'cases' / 'sample.toml').write_text('cases = [1]\n')
            git(repository.seed, 'add', '.')
            git(repository.seed, 'commit', '--quiet', '-m', 'Change synthetic source')
            git(repository.seed, 'push', '--quiet', 'origin', 'main')

            current = repository.client.head()
            source = repository.client.source(pinned, tmp_path / 'pinned')

            assert pinned == repository.revision
            assert current == git(repository.remote, 'rev-parse', 'main')
            assert current != pinned
            assert (source / 'sample.toml').read_bytes() == b'cases = []\r\n'

        @pytest.mark.parametrize('failure', ['public', 'unreadable', 'missing-main'])
        def test_refuses_an_unavailable_private_main(
            self,
            repository: PrivateRepository,
            monkeypatch: pytest.MonkeyPatch,
            failure: str,
        ) -> None:
            if failure == 'public':
                metadata = json.loads(repository.metadata.read_text())
                metadata['private'] = False
                repository.metadata.write_text(json.dumps(metadata))
            elif failure == 'unreadable':
                monkeypatch.setenv('TEST_GH_STATUS', '1')
            else:
                git(repository.remote, 'update-ref', '-d', 'refs/heads/main')

            with pytest.raises(InputError):
                repository.client.head()

    class TestSource:
        def test_materializes_only_case_sources_at_the_requested_revision(
            self, repository: PrivateRepository, tmp_path: Path
        ) -> None:
            (repository.seed / 'cases' / 'sample.toml').write_text('cases = [1]\n')
            git(repository.seed, 'add', '.')
            git(repository.seed, 'commit', '--quiet', '-m', 'Change synthetic source')
            git(repository.seed, 'push', '--quiet', 'origin', 'main')

            source = repository.client.source(repository.revision, tmp_path / 'client')

            assert (source / 'sample.toml').read_bytes() == b'cases = []\r\n'
            assert not (tmp_path / 'client' / 'unrelated.txt').exists()
            assert source.is_relative_to(tmp_path / 'client')

    class TestPublishTrials:
        def test_publishes_the_whole_batch_in_one_native_commit_in_input_order(
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
        ) -> None:
            second = trial.manifest.model_copy(update={'artifact_id': 'artifact-b'})
            archives = [
                MaterializedTrial(trial.job, second),
                MaterializedTrial(trial.job, trial.manifest),
            ]

            references = repository.client.publish_trials(archives)

            head = git(repository.remote, 'rev-parse', 'main')
            assert [reference.path for reference in references] == [
                archive.manifest.path for archive in archives
            ]
            assert {reference.commit_sha for reference in references} == {head}
            assert (
                git(repository.remote, 'rev-list', f'{repository.revision}..main')
                == head
            )
            expected_paths = {
                f'{archive.manifest.path}/{path}'
                for archive in archives
                for path in [
                    *(entry.path for entry in archive.manifest.files),
                    'manifest.json',
                ]
            }
            assert (
                set(
                    git(
                        repository.remote, 'diff', '--name-only', f'{head}^', head
                    ).splitlines()
                )
                == expected_paths
            )
            for archive, reference in zip(archives, references, strict=True):
                restored = repository.client.materialize(
                    reference, tmp_path / archive.manifest.artifact_id
                )
                assert restored.manifest == archive.manifest
                for entry in archive.manifest.files:
                    relative = Path(entry.path).relative_to('job')
                    assert contents(restored.job / relative) == contents(
                        archive.job / relative
                    ), entry.path

        def test_a_mixed_batch_keeps_original_references_and_repeats_without_writes(
            self, repository: PrivateRepository, trial: Trial
        ) -> None:
            original = publish(repository, trial)
            second = trial.manifest.model_copy(update={'artifact_id': 'artifact-b'})
            archives = [
                MaterializedTrial(trial.job, second),
                MaterializedTrial(trial.job, trial.manifest),
            ]

            references = repository.client.publish_trials(archives)
            repeated = repository.client.publish_trials(archives)

            assert references[1] == repeated[1] == original
            assert references == repeated
            assert references[0].commit_sha != original.commit_sha
            assert (
                git(repository.remote, 'rev-parse', 'main') == references[0].commit_sha
            )
            assert git(repository.remote, 'rev-parse', 'main^') == original.commit_sha
            assert (
                git(
                    repository.remote,
                    'show',
                    f'{original.commit_sha}:{original.path}/manifest.json',
                )
                == trial.manifest.content().decode().strip()
            )

        def test_an_empty_batch_needs_no_remote_access(
            self, repository: PrivateRepository, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv('TEST_GH_STATUS', '1')

            references = repository.client.publish_trials([])

            assert references == ()
            assert git(repository.remote, 'rev-parse', 'main') == repository.revision

        @pytest.mark.parametrize(
            'conflicting', [False, True], ids=['same', 'different']
        )
        def test_refuses_repeated_paths_before_publishing_either_archive(
            self, repository: PrivateRepository, trial: Trial, conflicting: bool
        ) -> None:
            second = trial.manifest.model_copy(
                update={'scope': 'other'} if conflicting else {}
            )
            archives = [
                MaterializedTrial(trial.job, trial.manifest),
                MaterializedTrial(trial.job, second),
            ]

            with pytest.raises(InputError):
                repository.client.publish_trials(archives)

            assert git(repository.remote, 'rev-parse', 'main') == repository.revision

        def test_a_later_changed_archive_prevents_the_entire_batch_push(
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
        ) -> None:
            second = trial.manifest.model_copy(update={'artifact_id': 'artifact-b'})
            changed = tmp_path / 'changed-job'
            shutil.copytree(trial.job, changed, symlinks=True)
            (changed / trial.result.trial_name / 'agent' / 'output.txt').write_text(
                'Changed after audit.\n'
            )

            with pytest.raises(InputError):
                repository.client.publish_trials(
                    [
                        MaterializedTrial(trial.job, trial.manifest),
                        MaterializedTrial(changed, second),
                    ]
                )

            assert git(repository.remote, 'rev-parse', 'main') == repository.revision

        @pytest.mark.parametrize(
            'failure',
            ['public', 'unreadable', 'wrong-repository', 'coerced-visibility'],
        )
        def test_checks_private_visibility_before_pushing_any_artifact(
            self,
            repository: PrivateRepository,
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
            self, repository: PrivateRepository, trial: Trial
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

        @pytest.mark.parametrize('ancestor', ['runs', 'runs/run-a/trials'])
        def test_remote_ancestor_symlinks_cannot_write_outside_the_clone(
            self,
            repository: PrivateRepository,
            trial: Trial,
            tmp_path: Path,
            ancestor: str,
        ) -> None:
            outside = tmp_path / 'outside'
            outside.mkdir()
            sentinel = outside / 'sentinel.txt'
            sentinel.write_bytes(b'Leave this external tree unchanged.\n')
            link = repository.seed / ancestor
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(outside, target_is_directory=True)
            git(repository.seed, 'add', ancestor)
            git(
                repository.seed,
                'commit',
                '--quiet',
                '-m',
                'Add synthetic ancestor link',
            )
            git(repository.seed, 'push', '--quiet', 'origin', 'main')
            before = git(repository.remote, 'rev-parse', 'main')

            with pytest.raises(InputError):
                publish(repository, trial)

            assert sentinel.read_bytes() == b'Leave this external tree unchanged.\n'
            assert sorted(
                path.relative_to(outside).as_posix() for path in outside.rglob('*')
            ) == ['sentinel.txt']
            assert git(repository.remote, 'rev-parse', 'main') == before

        def test_workspace_git_attributes_cannot_normalize_archived_bytes(
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
        ) -> None:
            workspace = trial.job / trial.result.trial_name / 'artifacts' / 'app'
            (workspace / '.gitattributes').write_text('* text eol=lf\n')
            manifest = snapshot(trial)

            (reference,) = repository.client.publish_trials(
                [MaterializedTrial(trial.job, manifest)]
            )
            restored = repository.client.materialize(reference, tmp_path / 'restored')

            archived = restored.job / trial.result.trial_name / 'artifacts' / 'app'
            assert (archived / 'answer.txt').read_bytes() == b'Answer.\r\n'
            assert (archived / '.gitattributes').read_bytes() == b'* text eol=lf\n'

        def test_does_not_return_a_durable_reference_when_the_push_is_rejected(
            self, repository: PrivateRepository, trial: Trial
        ) -> None:
            before = git(repository.remote, 'rev-parse', 'main')
            second = trial.manifest.model_copy(update={'artifact_id': 'artifact-b'})
            hook = repository.remote / 'hooks' / 'pre-receive'
            hook.write_text('#!/bin/sh\nexit 1\n')
            hook.chmod(0o755)

            with pytest.raises(InputError):
                repository.client.publish_trials(
                    [
                        MaterializedTrial(trial.job, trial.manifest),
                        MaterializedTrial(trial.job, second),
                    ]
                )

            assert git(repository.remote, 'rev-parse', 'main') == before

        def test_a_successful_push_acknowledgment_requires_remote_confirmation(
            self, repository: PrivateRepository, trial: Trial
        ) -> None:
            second = trial.manifest.model_copy(update={'artifact_id': 'artifact-b'})
            hook = repository.remote / 'hooks' / 'post-receive'
            hook.write_text(
                '#!/bin/bash\n'
                'set -euo pipefail\n'
                'read -r old new ref\n'
                'git update-ref "$ref" "$old" "$new"\n'
            )
            hook.chmod(0o755)

            with pytest.raises(InputError):
                repository.client.publish_trials(
                    [
                        MaterializedTrial(trial.job, trial.manifest),
                        MaterializedTrial(trial.job, second),
                    ]
                )

            assert git(repository.remote, 'rev-parse', 'main') == repository.revision

        @pytest.mark.parametrize('change', ['content', 'outside'])
        def test_commit_hooks_cannot_publish_changes_beyond_the_audited_tree(
            self,
            repository: PrivateRepository,
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

        def test_refuses_overwriting_an_artifact_with_different_content(
            self, repository: PrivateRepository, trial: Trial
        ) -> None:
            reference = publish(repository, trial)
            result = trial.job / trial.result.trial_name / 'agent' / 'output.txt'
            result.write_text('Changed answer.\n')
            changed = build_manifest(
                trial.job,
                scope='sample',
                run_id='run-a',
                artifact_id='artifact-a',
                trial_id=str(trial.result.id),
                job_name=trial.job.name,
                trial_name=trial.result.trial_name,
                code_revision=CODE_REVISION,
                case_revision=repository.revision,
            )

            with pytest.raises(InputError):
                repository.client.publish_trials(
                    [MaterializedTrial(trial.job, changed)]
                )

            assert git(repository.remote, 'rev-parse', 'main') == reference.commit_sha

        @pytest.mark.parametrize('change', ['file', 'directory'])
        def test_rechecks_selected_files_and_directories_after_the_manifest_was_built(
            self, repository: PrivateRepository, trial: Trial, change: str
        ) -> None:
            before = git(repository.remote, 'rev-parse', 'main')
            root = trial.job / trial.result.trial_name
            if change == 'file':
                (root / 'agent' / 'output.txt').write_text('Changed after audit.\n')
            else:
                (root / 'artifacts' / 'new-empty').mkdir()

            with pytest.raises(InputError):
                publish(repository, trial)

            assert git(repository.remote, 'rev-parse', 'main') == before

        def test_concurrent_batches_preserve_every_immutable_trial(
            self,
            repository: PrivateRepository,
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
                    'artifact_id': 'artifact-b',
                }
            )
            third = trial.manifest.model_copy(
                update={'run_id': 'run-c', 'artifact_id': 'artifact-c'}
            )
            fourth = trial.manifest.model_copy(
                update={'run_id': 'run-d', 'artifact_id': 'artifact-d'}
            )

            def push(manifests: tuple[TrialManifest, ...]) -> tuple[ArtifactRef, ...]:
                return ArtifactRepository(SLUG).publish_trials(
                    [MaterializedTrial(trial.job, manifest) for manifest in manifests]
                )

            with ThreadPoolExecutor(max_workers=2) as writers:
                batches = list(
                    writers.map(push, [(trial.manifest, second), (third, fourth)])
                )

            references = [reference for batch in batches for reference in batch]
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
                    restored.job / trial.result.trial_name / 'agent' / 'output.txt'
                ).read_bytes() == (
                    trial.job / trial.result.trial_name / 'agent' / 'output.txt'
                ).read_bytes()
            assert git(repository.remote, 'show', 'main:unrelated.txt') == (
                'Not a case source.'
            )

    class TestMaterialize:
        def test_preserves_frozen_empty_directories_after_the_original_client_is_gone(
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
        ) -> None:
            root = trial.job / trial.result.trial_name
            shutil.rmtree(root / 'artifacts')
            empty = ('artifacts', 'agent/logs/artifacts', 'verifier/logs')
            for relative in empty:
                (root / relative).mkdir(parents=True, exist_ok=True)
            frozen = tmp_path / 'frozen'
            snapshot_trial(trial.job, frozen, trial.result.trial_name)
            manifest = snapshot(replace(trial, job=frozen))
            shutil.rmtree(trial.job.parent)

            (reference,) = repository.client.publish_trials(
                [MaterializedTrial(frozen, manifest)]
            )
            shutil.rmtree(frozen)
            restored = repository.client.materialize(reference, tmp_path / 'fresh')

            assert restored.manifest == manifest
            for relative in empty:
                directory = restored.job / trial.result.trial_name / relative
                assert directory.is_dir() and not directory.is_symlink(), relative
                assert list(directory.iterdir()) == [], relative
                assert (
                    f'job/{trial.result.trial_name}/{relative}' in manifest.directories
                )
            for relative in manifest.directories:
                assert (restored.job / Path(relative).relative_to('job')).is_dir()
            assert {
                path.relative_to(restored.job).as_posix()
                for path in restored.job.rglob('*')
                if path.is_file() or path.is_symlink()
            } == {entry.path.removeprefix('job/') for entry in manifest.files}

        def test_unknown_historical_revisions_survive_publication_and_restoration(
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
        ) -> None:
            manifest = TrialManifest.model_validate(
                {
                    **trial.manifest.model_dump(),
                    'code_revision': None,
                    'case_revision': None,
                }
            )
            for field in ['code_revision', 'case_revision']:
                with pytest.raises(ValidationError):
                    TrialManifest.model_validate(
                        {**manifest.model_dump(), field: 'invalid-commit'}
                    )

            (reference,) = repository.client.publish_trials(
                [MaterializedTrial(trial.job, manifest)]
            )
            restored = repository.client.materialize(reference, tmp_path / 'restored')

            archived = json.loads(
                git(
                    repository.remote,
                    'show',
                    f'{reference.commit_sha}:{reference.path}/manifest.json',
                )
            )
            assert archived['code_revision'] is archived['case_revision'] is None
            assert restored.manifest == manifest
            assert (restored.job / 'config.json').read_bytes() == (
                trial.job / 'config.json'
            ).read_bytes()

        def test_case_aliases_never_follow_a_previously_restored_symlink(
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
        ) -> None:
            outside = tmp_path / 'outside'
            outside.mkdir()
            (outside / 'sentinel.txt').write_bytes(
                b'Leave this external tree unchanged.\n'
            )
            reference = publish(repository, trial)
            prefix = f'job/{trial.result.trial_name}/artifacts/app'
            additions = {
                f'{prefix}/A': ('120000', os.fsencode(outside)),
                f'{prefix}/a/file.txt': ('100644', b'Keep these artifact bytes.\n'),
            }
            entries = [
                FileEntry.model_validate(
                    {
                        'path': path,
                        'mode': mode,
                        'sha256': hashlib.sha256(content).hexdigest(),
                    }
                )
                for path, (mode, content) in additions.items()
            ]
            payload = trial.manifest.model_dump(mode='json')
            payload['files'] = [
                entry.model_dump(mode='json')
                for entry in sorted(
                    [*trial.manifest.files, *entries], key=lambda item: item.path
                )
            ]
            content = f'{json.dumps(payload, indent=2)}\n'.encode()
            additions['manifest.json'] = ('100644', content)
            git(repository.remote, 'read-tree', reference.commit_sha)
            for path, (mode, content) in additions.items():
                blob = (
                    subprocess.run(
                        ['git', 'hash-object', '-w', '--stdin'],
                        cwd=repository.remote,
                        input=content,
                        capture_output=True,
                        check=True,
                    )
                    .stdout.decode()
                    .strip()
                )
                git(
                    repository.remote,
                    'update-index',
                    '--add',
                    '--cacheinfo',
                    f'{mode},{blob},{reference.path}/{path}',
                )
            tree = git(repository.remote, 'write-tree')
            commit = git(
                repository.remote,
                'commit-tree',
                tree,
                '-p',
                reference.commit_sha,
                '-m',
                'Add synthetic case alias paths',
            )
            git(
                repository.remote,
                'update-ref',
                'refs/heads/main',
                commit,
                reference.commit_sha,
            )
            changed = ArtifactRef(
                commit_sha=commit,
                path=reference.path,
                manifest_digest=hashlib.sha256(content).hexdigest(),
            )

            with pytest.raises(InputError):
                repository.client.materialize(changed, tmp_path / 'restored')

            assert sorted(path.name for path in outside.iterdir()) == ['sentinel.txt']
            assert (
                outside / 'sentinel.txt'
            ).read_bytes() == b'Leave this external tree unchanged.\n'
            assert not (tmp_path / 'restored').exists()

        @pytest.mark.parametrize(
            'failure', ['bytes', 'mode', 'manifest', 'extra', 'missing']
        )
        def test_refuses_remote_trees_that_differ_from_the_recorded_manifest(
            self,
            repository: PrivateRepository,
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
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
        ) -> None:
            outside = tmp_path / 'outside.txt'
            outside.write_bytes(b'Private sentinel, never archive this file.\n')
            workspace = trial.job / trial.result.trial_name / 'artifacts' / 'app'
            (workspace / 'external').symlink_to(outside)
            manifest = snapshot(trial)
            (reference,) = repository.client.publish_trials(
                [MaterializedTrial(trial.job, manifest)]
            )

            restored = repository.client.materialize(reference, tmp_path / 'restored')

            link = (
                restored.job
                / trial.result.trial_name
                / 'artifacts'
                / 'app'
                / 'external'
            )
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
            self, repository: PrivateRepository, trial: Trial, tmp_path: Path
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
                path = restored.job / Path(item.path).relative_to('job')
                assert contents(path) == originals[item.path], item.path
                assert path.is_symlink() == (item.mode == '120000'), item.path
                if not path.is_symlink():
                    assert bool(path.stat().st_mode & stat.S_IXUSR) == (
                        item.mode == '100755'
                    ), item.path
            workspace = restored.job / trial.result.trial_name / 'artifacts' / 'app'
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
            repository: PrivateRepository,
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
            shutil.copytree(restored.job, prepared, symlinks=True)
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
                root=tmp_path,
                sources=[],
            )
            plan = asyncio.run(JobPlan.from_config(current))
            source = SourceJobConfig(action='diff', type='local', path=prepared)

            diff = compare_job_trials(plan, load_source_trials([(source, prepared)]))

            assert [(item.task_name, item.action) for item in diff.trials] == [
                ('f/brief', expected)
            ]
            assert collection.read_trials(trial.family, prepared) == original
