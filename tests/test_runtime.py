from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import httpx
import pytest
import tomli_w
from typer.testing import CliRunner

from assistant_bench import cli, runtime
from assistant_bench.artifacts import ArtifactRepository
from assistant_bench.catalog import NixosConfigExport, System
from assistant_bench.messages import InputError
from assistant_bench.outputs import write_outputs
from assistant_bench.runtime import RunContext
from support import (
    PrivateRepository,
    commit_all,
    git,
    make_system,
    new_repository,
    write_votes_family,
)


REVISION = 'a' * 40
runner = CliRunner()


@dataclass(frozen=True)
class CanonicalRepository:
    root: Path
    seed: Path
    remote: Path
    revision: str


@pytest.fixture
def canonical_repository(
    private_repository: PrivateRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> CanonicalRepository:
    seed = private_repository.seed
    write_votes_family(seed / 'families', ['acme-large'], {'greeting': 'message'})
    revision = commit_all(seed, 'Add public cases')
    git(seed, 'push', '--quiet', 'origin', 'main')
    root = tmp_path / 'implementation'
    git(tmp_path, 'clone', '--quiet', str(private_repository.remote), str(root))
    unrelated = tmp_path / 'unrelated.git'
    git(tmp_path, 'init', '--quiet', '--bare', '--initial-branch=main', str(unrelated))
    git(root, 'remote', 'set-url', 'origin', str(unrelated))
    config = tmp_path / 'canonical.gitconfig'
    git(
        tmp_path,
        'config',
        '--file',
        str(config),
        f'url.{private_repository.remote}.insteadOf',
        runtime.PUBLIC_REPOSITORY,
    )
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    monkeypatch.setattr(runtime, 'REPO_ROOT', root)
    return CanonicalRepository(root, seed, private_repository.remote, revision)


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    monkeypatch.setenv(runtime.REPOSITORY_VAR, 'example/private-data')
    head = Mock(return_value=REVISION)

    def restore(revision: str, destination: Path) -> Path:
        root = destination / 'cases'
        root.mkdir(parents=True)
        (root / 'f.toml').write_text(
            tomli_w.dumps(
                {
                    'cases': [
                        {
                            'id': 'held-out',
                            'category': 'message',
                            'source': 'held-out',
                            'instruction': 'Write a short greeting.',
                        }
                    ]
                }
            )
        )
        return root

    source = Mock(side_effect=restore)
    monkeypatch.setattr(ArtifactRepository, 'head', head)
    monkeypatch.setattr(ArtifactRepository, 'source', source)
    return head, source


@pytest.fixture
def catalog(export: NixosConfigExport, monkeypatch: pytest.MonkeyPatch) -> None:
    def load_systems(export: NixosConfigExport) -> dict[str, System]:
        return {'acme-large': make_system()}

    monkeypatch.setattr(cli, 'load_systems', load_systems)


class TestOpenSources:
    def test_pins_once_and_removes_the_materialized_sources_on_exit(
        self, repository: tuple[Mock, Mock]
    ) -> None:
        head, source = repository
        head.side_effect = [REVISION, 'b' * 40]

        with runtime.open_sources() as sources:
            root = sources.root
            assert sources.revision == REVISION
            assert 'Write a short greeting.' in (root / 'f.toml').read_text()

        head.assert_called_once_with()
        assert source.call_args.args[0] == REVISION
        assert not root.parent.exists()

    @pytest.mark.parametrize('failure', ['restore', 'caller'])
    def test_removes_partial_sources_after_an_error(
        self, repository: tuple[Mock, Mock], failure: str
    ) -> None:
        _, source = repository
        restore = cast(Callable[[str, Path], Path], source.side_effect)
        roots: list[Path] = []

        def failed_restore(revision: str, destination: Path) -> Path:
            root = restore(revision, destination)
            roots.append(root)
            if failure == 'restore':
                raise InputError('Private sources could not be read.')
            return root

        source.side_effect = failed_restore

        with (
            pytest.raises(InputError, match='Private sources could not be read'),
            runtime.open_sources(),
        ):
            raise InputError('Private sources could not be read.')

        assert roots
        assert all(not root.parent.exists() for root in roots)

    def test_requires_explicit_repository_configuration(
        self, repository: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        head, source = repository
        monkeypatch.delenv(runtime.REPOSITORY_VAR)

        with (
            pytest.raises(InputError, match=runtime.REPOSITORY_VAR),
            runtime.open_sources(),
        ):
            pytest.fail('A missing private repository was accepted.')

        head.assert_not_called()
        source.assert_not_called()


class TestCheckSources:
    @pytest.mark.usefixtures('catalog')
    @pytest.mark.parametrize('arguments', [('check', 'f'), ('fingerprints',)])
    def test_public_commands_ignore_local_cases_without_cloud_access(
        self,
        families_dir: Path,
        data_dir: Path,
        repository: tuple[Mock, Mock],
        monkeypatch: pytest.MonkeyPatch,
        arguments: tuple[str, ...],
    ) -> None:
        write_votes_family(families_dir, ['acme-large'], {'public': 'message'})
        local = data_dir / 'cases'
        local.mkdir(parents=True)
        (local / 'f.toml').write_text('This is not valid TOML.')
        (local / 'unread.jsonl').write_text('Unread local cases.')
        head, source = repository
        head.side_effect = AssertionError('Public validation reached private Git.')
        source.side_effect = AssertionError('Public validation read private sources.')
        send = Mock(side_effect=AssertionError('Public validation reached D1.'))
        monkeypatch.setattr(httpx.Client, 'send', send)
        monkeypatch.delenv(runtime.REPOSITORY_VAR)

        result = runner.invoke(cli.app, list(arguments))
        assert result.exit_code == 0, result.output

        expected = (
            'Family `f`: 1 valid case.'
            if arguments[0] == 'check'
            else 'Every system keeps the fingerprint its closed reports froze.'
        )
        assert expected in result.output
        head.assert_not_called()
        source.assert_not_called()

        send.assert_not_called()

    @pytest.mark.usefixtures('catalog')
    @pytest.mark.parametrize('duplicate', [False, True], ids=['combined', 'duplicate'])
    def test_private_check_validates_the_combined_sources_without_d1(
        self,
        families_dir: Path,
        repository: tuple[Mock, Mock],
        monkeypatch: pytest.MonkeyPatch,
        duplicate: bool,
    ) -> None:
        case_id = 'held-out' if duplicate else 'public'
        write_votes_family(families_dir, ['acme-large'], {case_id: 'message'})
        head, source = repository
        send = Mock(side_effect=AssertionError('Private case validation reached D1.'))
        monkeypatch.setattr(httpx.Client, 'send', send)

        result = runner.invoke(cli.app, ['check', 'f', '--private'])

        if duplicate:
            assert result.exit_code == 1, result.output
            assert 'case `held-out` appears at' in result.output
        else:
            assert result.exit_code == 0, result.output
            assert 'Family `f`: 2 valid cases.' in result.output
        head.assert_called_once_with()
        assert source.call_args.args[0] == REVISION
        assert not source.call_args.args[1].parent.exists()
        send.assert_not_called()

    @pytest.mark.usefixtures('catalog')
    def test_private_authentication_failure_never_falls_back_to_public_cases(
        self, families_dir: Path, repository: tuple[Mock, Mock]
    ) -> None:
        write_votes_family(families_dir, ['acme-large'], {'public': 'message'})
        head, source = repository
        head.side_effect = InputError('Private repository authentication failed.')

        result = runner.invoke(cli.app, ['check', 'f', '--private'])

        assert result.exit_code == 1, result.output
        assert 'Private repository authentication failed.' in result.output
        assert 'valid case' not in result.output
        source.assert_not_called()


class TestImplementationRevision:
    @pytest.mark.parametrize(
        'candidate', ['published-ancestor', 'unpublished', 'unrelated']
    )
    def test_requires_the_clean_head_to_be_reachable_from_canonical_main(
        self,
        canonical_repository: CanonicalRepository,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        candidate: str,
    ) -> None:
        source = canonical_repository
        (source.seed / 'NOTICE').write_text('A later canonical commit.\n')
        current = commit_all(source.seed, 'Advance canonical main')
        git(source.seed, 'push', '--quiet', 'origin', 'main')
        assert current != source.revision
        root = source.root
        if candidate == 'unpublished':
            (root / 'unpublished.txt').write_text('A clean local commit.\n')
            commit_all(root, 'Add an unpublished change')
            git(root, 'push', '--quiet', 'origin', 'HEAD:main')
        elif candidate == 'unrelated':
            root = tmp_path / 'unrelated-implementation'
            new_repository(root, {'NOTICE': 'An unrelated history.\n'})
            git(root, 'remote', 'add', 'origin', str(source.remote))
            monkeypatch.setattr(runtime, 'REPO_ROOT', root)
        assert git(root, 'status', '--porcelain') == ''

        if candidate == 'published-ancestor':
            assert runtime.implementation_revision() == source.revision
        else:
            with pytest.raises(InputError):
                runtime.implementation_revision()


class TestClosingSources:
    def test_fetches_the_frozen_public_case_from_the_canonical_repository(
        self, canonical_repository: CanonicalRepository, run_context: RunContext
    ) -> None:
        source = canonical_repository
        document = source.seed / 'families' / 'f' / 'cases.toml'
        expected = document.read_text().replace(
            'Write about greeting.', 'Pinned greeting.'
        )
        document.write_text(expected)
        revision = commit_all(source.seed, 'Pin a public case revision')
        git(source.seed, 'push', '--quiet', 'origin', 'main')
        generation = write_outputs(
            'f',
            [],
            run_context.state,
            previous=None,
            code_revision=revision,
            case_revision=run_context.case_revision,
            system_names={'acme-large': 'Acme Large'},
        )

        sources = run_context.closing_sources(generation)

        assert [case.instruction for case in sources.cases] == ['Pinned greeting.']
        assert document.read_text() == expected
