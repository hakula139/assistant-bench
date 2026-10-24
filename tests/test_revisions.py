import subprocess
from pathlib import Path
from typing import Any

import pytest

from assistant_bench import revisions
from assistant_bench.generation import Generation
from assistant_bench.paths import REPO_ROOT
from support import EXPORT_FILE, make_system, write_case_family, write_task_family


PROJECT = """\
[tool.assistant-bench]
generation-protocol = 1
"""
LOCK = """\
[[package]]
name = "harbor"
version = "0.23.0"
"""
SYSTEMS = """\
[large]
model = "acme-large"
"""
FAMILIES = {
    'families/writing/family.toml': 'systems = ["large"]\n',
    'families/coding/family.toml': 'systems = ["large"]\nharness = "terminus-2"\n',
    'families/smoke/family.toml': 'scorer = "tests"\n',
}


class TestCurrentSystems:
    def test_names_an_undefined_system_and_its_family(self, families_dir: Path) -> None:
        write_case_family(families_dir, ['missing'], name='writing')

        with pytest.raises(
            ValueError, match='Family `writing`.*`missing`.*systems.toml'
        ):
            revisions.current_systems({})

    def test_resolves_only_listed_systems_under_each_family_harness(
        self, families_dir: Path
    ) -> None:
        write_case_family(families_dir, ['large'], name='writing')
        write_task_family(families_dir, 'echo', name='coding', systems=('large',))
        write_task_family(families_dir, name='smoke')
        system = make_system(name='large')

        current = revisions.current_systems(
            {'large': system, 'author-only': make_system(name='author-only')}
        )

        assert current == {
            'writing/large': system,
            'coding/large': system.model_copy(update={'harness': 'terminus-2'}),
        }


class TestSystemsAt:
    @staticmethod
    def fake_git(
        monkeypatch: pytest.MonkeyPatch,
        project: str = PROJECT,
        systems: str = SYSTEMS,
    ) -> list[tuple[str, ...]]:
        documents = FAMILIES | {
            'pyproject.toml': project,
            'uv.lock': LOCK,
            'systems.toml': systems,
        }
        calls: list[tuple[str, ...]] = []

        def run(
            args: tuple[str, ...], **kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            assert kwargs == {
                'check': True,
                'stdout': subprocess.PIPE,
                'text': True,
                'cwd': REPO_ROOT,
            }
            match args[:2]:
                case ('git', 'rev-parse'):
                    output = 'abc123'
                case ('git', 'ls-tree'):
                    output = '\n'.join(FAMILIES)
                case ('git', 'show'):
                    output = documents[args[2].removeprefix('abc123:')]
                case ('nix', 'build'):
                    output = str(EXPORT_FILE)
                case _:
                    pytest.fail(f'Unexpected command {args}')
            return subprocess.CompletedProcess(args, 0, stdout=f'{output}\n')

        monkeypatch.setattr(subprocess, 'run', run)
        return calls

    def test_uses_historical_export_versions_and_family_harnesses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self.fake_git(monkeypatch)
        historical = Generation(protocol=1, harbor='0.23.0')

        systems = revisions.systems_at(
            'origin/main', {'writing/large', 'coding/large', 'new/large'}
        )

        assert systems == {
            'writing/large': make_system(name='large', generation=historical),
            'coding/large': make_system(
                name='large', generation=historical, harness='terminus-2'
            ),
        }
        assert ('git', 'show', 'abc123:pyproject.toml') in calls
        assert ('git', 'show', 'abc123:uv.lock') in calls
        assert calls[-1] == (
            'nix',
            'build',
            '--no-link',
            '--print-out-paths',
            f'git+file:{REPO_ROOT}?rev=abc123#nixos-config',
        )

    def test_requires_no_historical_metadata_after_all_shared_names_are_replaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self.fake_git(monkeypatch, project='')

        systems = revisions.systems_at('origin/main', {'writing/large-high'})

        assert systems == {}
        assert not any(args[:2] == ('nix', 'build') for args in calls)
        assert ('git', 'show', 'abc123:pyproject.toml') not in calls

    def test_detects_transport_changes_against_the_revision_owned_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fake_git(monkeypatch)
        generation = Generation(protocol=1, harbor='0.23.0')
        head = {
            'writing/large': make_system(
                name='large', generation=generation, api='openai-responses'
            ),
            'coding/large': make_system(
                name='large',
                harness='terminus-2',
                generation=generation,
                api='openai-responses',
            ),
        }

        base = revisions.systems_at('origin/main', set(head))

        assert revisions.changed_fingerprints(base, head) == list(head)
        assert all(system.api == 'openai-chat-completions' for system in base.values())

        self.fake_git(monkeypatch, systems=SYSTEMS + 'api = "openai-responses"\n')

        responses_base = revisions.systems_at('origin/main', set(head))

        assert revisions.changed_fingerprints(responses_base, head) == []

    def test_requires_a_new_name_when_a_shared_cohort_has_no_declared_protocol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fake_git(monkeypatch, project='')

        with pytest.raises(
            ValueError, match='no declared generation protocol.*new names'
        ):
            revisions.systems_at('origin/main', {'writing/large'})

    @pytest.mark.parametrize(
        ('generation', 'expected'),
        [
            pytest.param(
                Generation(protocol=1, harbor='0.24.0'),
                ['coding/large'],
                id='harbor-native-prompts',
            ),
            pytest.param(
                Generation(protocol=2, harbor='0.23.0'),
                ['writing/large', 'coding/large'],
                id='generation-protocol',
            ),
        ],
    )
    def test_detects_runtime_changes_against_revision_owned_versions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        generation: Generation,
        expected: list[str],
    ) -> None:
        self.fake_git(monkeypatch)
        head = {
            'writing/large': make_system(name='large', generation=generation),
            'coding/large': make_system(
                name='large', harness='terminus-2', generation=generation
            ),
        }

        base = revisions.systems_at('origin/main', set(head))

        assert revisions.changed_fingerprints(base, head) == expected


class TestChangedFingerprints:
    def test_names_only_systems_that_kept_their_name(self) -> None:
        base = {'kept': make_system(), 'changed': make_system(), 'gone': make_system()}
        head = {
            'kept': make_system(),
            'changed': make_system(effort='high'),
            'new': make_system(effort='low'),
        }

        assert revisions.changed_fingerprints(base, head) == ['changed']
