import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from assistant_bench import ci


def git(*args: str) -> str:
    return subprocess.run(
        ['git', *args], check=True, stdout=subprocess.PIPE, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.chdir(tmp_path)
    git('init', '--quiet')
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'Test')
    for task in ('a', 'b'):
        directory = Path('families/coding-repo/tasks') / task
        directory.mkdir(parents=True)
        (directory / 'instruction.md').write_text(f'Task {task}.')
        (directory / 'task.toml').write_text('version = "1.0.0"')
    git('add', '.')
    git('commit', '--quiet', '-m', 'Initial tasks')
    return git('rev-parse', 'HEAD')


class TestSelectedTasks:
    @pytest.mark.parametrize(
        ('changes', 'expected'),
        [
            (
                {'families/coding-repo/tasks/a/instruction.md': 'Changed'},
                ['a'],
            ),
            (
                {
                    'families/coding-repo/tasks/a/tests/test file.py': 'Changed',
                    'families/coding-repo/tasks/a/instruction.md': 'Changed',
                    'families/coding-repo/tasks/b/task.toml': 'Changed',
                },
                ['a', 'b'],
            ),
            (
                {'families/coding-repo/tasks/new/instruction.md': 'New'},
                ['new'],
            ),
            (
                {
                    'families/coding-repo/tasks/a/instruction.md': None,
                    'families/coding-repo/tasks/a/task.toml': None,
                },
                [],
            ),
            (
                {
                    'families/coding-repo/tasks/a/instruction.md': None,
                    'families/coding-repo/tasks/a/task.toml': None,
                    'families/coding-repo/tasks/renamed/instruction.md': 'Task a.',
                    'families/coding-repo/tasks/renamed/task.toml': 'version = "1.0.0"',
                },
                ['renamed'],
            ),
            ({'docs/guide/authoring.md': 'Documentation'}, []),
            ({'tests/test_jobs.py': 'Tests'}, []),
            ({'web/src/index.ts': 'Site'}, []),
            ({'families/smoke/tasks/echo/instruction.md': 'Smoke'}, []),
            ({'src/assistant_bench/jobs.py': 'Job'}, None),
            ({'src/assistant_bench/families.py': 'Validation'}, None),
            ({'src/assistant_bench/harnesses/terminus.py': 'Harness'}, None),
            ({'src/assistant_bench/task_assets.py': 'Generator'}, None),
            ({'families/coding-repo/shared/tests/verify_lib.py': 'Verifier'}, None),
            ({'families/coding-repo/shared/environment/Dockerfile': 'Image'}, None),
            ({'families/coding-repo/shared/tests/test.sh': 'Runner'}, None),
            ({'families/coding-repo/shared/tests/new_probe.py': 'New asset'}, None),
            ({'families/coding-repo/shared-notes/README.md': 'Notes'}, []),
            ({'uv.lock': 'Harbor dependency'}, None),
            ({'pyproject.toml': 'Runtime dependencies'}, None),
            ({'.python-version': 'Python'}, None),
            ({'flake.nix': 'Dev shell'}, None),
            ({'flake.lock': 'Pinned inputs'}, None),
            ({'systems.toml': 'Catalog'}, None),
            ({'families/coding-repo/family.toml': 'Family settings'}, None),
            ({'.github/workflows/ci.yml': 'Workflow'}, None),
            ({'.github/actions/setup-nix/action.yml': 'Setup'}, None),
            ({'src/assistant_bench/ci.py': 'Selector'}, None),
        ],
        ids=[
            'one-task',
            'several-tasks-and-files',
            'added-task',
            'deleted-task',
            'renamed-task',
            'documentation',
            'tests',
            'site',
            'another-family',
            'jobs',
            'family-validation',
            'harness',
            'asset-generator',
            'shared-verifier',
            'shared-image',
            'shared-runner',
            'new-shared-asset',
            'neighboring-directory',
            'harbor',
            'dependencies',
            'python',
            'dev-shell',
            'nix-inputs',
            'systems',
            'family-settings',
            'workflow',
            'setup-action',
            'selector',
        ],
    )
    def test_selects_current_tasks_affected_since_the_base(
        self,
        repository: str,
        changes: dict[str, str | None],
        expected: list[str] | None,
    ) -> None:
        for name, content in changes.items():
            path = Path(name)
            if content is None:
                path.unlink()
                if not any(path.parent.iterdir()):
                    path.parent.rmdir()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        git('add', '.')
        git('commit', '--quiet', '-m', 'Change inputs')

        selected = ci.selected_tasks(repository)

        assert selected == expected

    def test_checks_every_task_without_a_base(self) -> None:
        assert ci.selected_tasks(None) is None

    def test_reports_an_invalid_base(self, repository: str) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            ci.selected_tasks('missing-base')


class TestMain:
    def test_skips_trials_when_the_module_has_no_affected_task(
        self, repository: str
    ) -> None:
        result = subprocess.run(
            [sys.executable, '-m', 'assistant_bench.ci', repository],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )

        assert result.stdout == 'No coding tasks changed.\n'

    @pytest.mark.parametrize(
        ('tasks', 'expected'),
        [
            (None, ['bench', 'check', 'coding-repo']),
            (
                ['a', 'b'],
                ['bench', 'check', 'coding-repo', '--task', 'a', '--task', 'b'],
            ),
            ([], None),
        ],
        ids=['all-tasks', 'changed-tasks', 'no-tasks'],
    )
    def test_runs_only_the_selected_checks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tasks: list[str] | None,
        expected: list[str] | None,
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(sys, 'argv', ['assistant_bench.ci', 'base'])

        def selected_tasks(base: str | None) -> list[str] | None:
            return tasks

        monkeypatch.setattr(ci, 'selected_tasks', selected_tasks)

        def run(command: list[str], **kwargs: Any) -> None:
            assert kwargs == {'check': True}
            calls.append(command)

        monkeypatch.setattr(subprocess, 'run', run)

        ci.main()

        assert calls == ([] if expected is None else [expected])
        if expected is None:
            assert capsys.readouterr().out == 'No coding tasks changed.\n'
