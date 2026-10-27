import subprocess
from pathlib import Path
from typing import Any

import pytest

from assistant_bench import report, revisions
from assistant_bench.harnesses import fingerprint
from assistant_bench.paths import REPO_ROOT
from assistant_bench.report import Report, write_report
from support import (
    REPORT_FIXTURE,
    make_system,
    write_case_family,
    write_task_family,
)


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


class TestCurrentFingerprints:
    def test_fingerprints_each_system_under_its_family_harness(
        self, families_dir: Path
    ) -> None:
        write_case_family(families_dir, ['large'], name='writing')
        write_task_family(families_dir, 'echo', name='coding', systems=('large',))
        system = make_system(name='large')

        current = revisions.current_fingerprints({'large': system})

        assert current == {
            'coding/large': fingerprint(
                system.model_copy(update={'harness': 'terminus-2'})
            ),
            'writing/large': fingerprint(system),
        }
        assert current['coding/large'] != current['writing/large']


class TestManifestAt:
    @staticmethod
    def fake_git(
        monkeypatch: pytest.MonkeyPatch, manifest: str | None
    ) -> list[tuple[str, ...]]:
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
                    output = '' if manifest is None else revisions.MANIFEST_FILE
                case ('git', 'show'):
                    assert args[2] == f'abc123:{revisions.MANIFEST_FILE}'
                    assert manifest is not None
                    output = manifest
                case _:
                    pytest.fail(f'Unexpected command {args}')
            return subprocess.CompletedProcess(args, 0, stdout=f'{output}\n')

        monkeypatch.setattr(subprocess, 'run', run)
        return calls

    def test_reads_the_manifest_the_revision_committed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fingerprints = {'writing/large': 'a1', 'coding/large': 'b2'}
        calls = self.fake_git(monkeypatch, revisions.render_manifest(fingerprints))

        manifest = revisions.manifest_at('origin/main')

        assert manifest == fingerprints
        assert calls[0] == ('git', 'rev-parse', '--verify', 'origin/main^{commit}')

    def test_reports_a_revision_without_a_manifest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self.fake_git(monkeypatch, None)

        manifest = revisions.manifest_at('origin/main')

        assert manifest is None
        assert not any(args[:2] == ('git', 'show') for args in calls)

    def test_rejects_a_manifest_that_does_not_map_names_to_fingerprints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fake_git(monkeypatch, '{"writing/large": 1}')

        with pytest.raises(ValueError):
            revisions.manifest_at('origin/main')


class TestChangedFingerprints:
    def test_names_only_systems_that_kept_their_name(self) -> None:
        base = {'f/kept': 'a', 'f/changed': 'b', 'f/gone': 'c'}
        head = {'f/kept': 'a', 'f/changed': 'x', 'f/new': 'y'}

        changed = revisions.changed_fingerprints(base, head)

        assert changed == ['f/changed']


class TestReported:
    @pytest.mark.parametrize(
        ('key', 'expected'),
        [
            pytest.param('arena/acme-large', True, id='reported'),
            pytest.param('arena/acme-new', False, id='other-system'),
            pytest.param('writing/acme-large', False, id='other-family'),
        ],
    )
    def test_requires_a_closed_report_of_the_same_family(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        key: str,
        expected: bool,
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        write_report(Report.model_validate_json(REPORT_FIXTURE.read_text()))

        reported = revisions.reported(key)

        assert reported is expected
