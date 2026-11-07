import asyncio
import tomllib
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.task import config as task_config
from harbor.models.task.task import Task

import assistant_bench.collection as collection
from assistant_bench import jobs
from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import (
    Family,
    case_task,
    load_cases,
    load_family,
)
from assistant_bench.paths import FAMILIES_DIR
from assistant_bench.tasks import (
    author_vendors,
    changed_tasks,
    check_tasks,
    render_task_config,
    render_tasks,
)
from support import (
    break_setting,
    finish_generation,
    finish_job,
    make_system,
    voted_tasks,
    with_settings,
    write_task_family,
)


def version(config: task_config.TaskConfig) -> str:
    assert config.task is not None
    assert config.task.version is not None
    return config.task.version


def installed_version(path: Path) -> str:
    return version(Task(path, disable_verification=True).config)


def planned_actions(job: JobConfig) -> list[tuple[str, DiffAction]]:
    """Return the action Harbor plans for each trial against the family's jobs."""
    plan = asyncio.run(JobPlan.from_config(job))
    sources: list[tuple[SourceJobConfig, Path]] = []
    for source in job.source_jobs:
        assert source.path is not None
        sources.append((source, source.path))
    diff = compare_job_trials(plan, load_source_trials(sources))
    return sorted((trial.task_name, trial.action) for trial in diff.trials)


def task_family(families_dir: Path, *task_ids: str) -> tuple[Family, list[Path]]:
    """Write task family `f` with a copy of the smoke echo task per ID."""
    write_task_family(families_dir, *task_ids)
    family = load_family('f')
    return family, [case_task(family, task_id) for task_id in task_ids]


class TestRenderTaskConfig:
    def test_pins_the_version_of_a_known_task(self) -> None:
        config = render_task_config('smoke-text/en-greeting', 'Hi.')

        assert config.agent.timeout_sec == 3600.0
        assert version(config) == '1.0.0+8e49aab92cda'

    def test_changes_the_version_with_the_agent_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = version(render_task_config('f/a', 'Hi.'))
        monkeypatch.setattr('assistant_bench.tasks.AGENT_TIMEOUT_SEC', 60.0)

        after = version(render_task_config('f/a', 'Hi.'))

        assert after != before


class TestRenderTasks:
    def test_writes_a_versioned_task(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        (case, _) = load_cases(family)

        (path,) = render_tasks(family, [case], root=data_dir)

        assert path == data_dir / 'tasks' / 'smoke-text' / 'en-greeting'
        assert installed_version(path) == version(
            render_task_config('smoke-text/en-greeting', case.instruction)
        )
        assert Task(path, disable_verification=True).instruction == case.instruction

    def test_reruns_an_edited_case_and_reuses_an_unedited_one(
        self, data_dir: Path
    ) -> None:
        family = load_family('smoke-text')
        cases = load_cases(family)
        first = finish_job(
            jobs.job(
                family,
                [make_system()],
                render_tasks(family, cases, root=data_dir),
                root=data_dir,
                sources=[],
            )
        )
        edited = [cases[0].model_copy(update={'instruction': 'Reworded.'}), cases[1]]

        job = jobs.job(
            family,
            [make_system()],
            render_tasks(family, edited, root=data_dir),
            root=data_dir,
            sources=[jobs.job_source(first)],
        )

        assert planned_actions(job) == [
            ('smoke-text/en-greeting', DiffAction.RERUN),
            ('smoke-text/zh-greeting', DiffAction.REUSE),
        ]

    def test_reruns_a_successful_case_at_the_new_deadline_then_reuses_its_success(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        system = make_system()
        family = with_settings(
            load_family('smoke-text'), systems=[system.name], reference=system.name
        )
        cases = load_cases(family)[:1]
        monkeypatch.setattr('assistant_bench.tasks.AGENT_TIMEOUT_SEC', 900.0)
        old = jobs.job(
            family,
            [system],
            render_tasks(family, cases, root=data_dir),
            root=data_dir,
            sources=[],
        ).model_copy(update={'job_name': 'old-deadline'})
        finish_generation(old, 'Earlier success.')
        first = jobs.job_path(old)
        monkeypatch.setattr('assistant_bench.tasks.AGENT_TIMEOUT_SEC', 3600.0)

        new = jobs.job(
            family,
            [system],
            render_tasks(family, cases, root=data_dir),
            root=data_dir,
            sources=[jobs.job_source(first)],
        ).model_copy(update={'job_name': 'new-deadline'})
        assert planned_actions(new) == [('smoke-text/en-greeting', DiffAction.RERUN)]
        finish_generation(new, 'Success at the current deadline.')

        rerun = jobs.job(
            family,
            [system],
            render_tasks(family, cases, root=data_dir),
            root=data_dir,
            sources=[jobs.job_source(first), jobs.job_source(jobs.job_path(new))],
        )
        assert planned_actions(rerun) == [('smoke-text/en-greeting', DiffAction.REUSE)]
        (output,) = collection.read_trials(family, jobs.job_path(new))
        assert output.text == 'Success at the current deadline.'
        assert output.job == 'new-deadline'

    def test_prunes_tasks_for_cases_the_family_no_longer_has(
        self, data_dir: Path
    ) -> None:
        family = load_family('smoke-text')
        stale = data_dir / 'tasks' / 'smoke-text' / 'en-greeting-v2'
        stale.mkdir(parents=True)
        (stale / 'instruction.md').write_text('Old.')
        (stale.parent / 'stray.md').write_text('Stray.')

        render_tasks(family, load_cases(family), root=data_dir)

        assert sorted(
            path.name for path in (data_dir / 'tasks' / 'smoke-text').iterdir()
        ) == ['en-greeting', 'zh-greeting']


class TestCheckTasks:
    def test_accepts_the_committed_smoke_task(self) -> None:
        family = load_family('smoke')

        check_tasks(family, [case_task(family, 'echo')])

    @pytest.mark.parametrize(
        ('path', 'value', 'message'),
        [
            pytest.param(
                ('task', 'version'), None, 'set `[task] version`.', id='no-version'
            ),
            pytest.param(
                ('task', 'version'),
                '1.0',
                '`[task] version` `1.0` is not a semantic version, such as `1.0.0`.',
                id='non-semver-version',
            ),
            pytest.param(
                ('agent', 'network_mode'),
                'public',
                'the agent phase needs `no-network`.',
                id='open-agent-network',
            ),
            pytest.param(
                ('verifier', 'network_mode'),
                'public',
                'the verifier phase needs `no-network`.',
                id='open-verifier-network',
            ),
            pytest.param(
                ('environment', 'network_mode'),
                'allowlist',
                'the agent phase needs `no-network`.',
                id='allowlist-environment',
            ),
            pytest.param(
                ('verifier', 'environment_mode'),
                'shared',
                'set `[verifier] environment_mode = "separate"`.',
                id='explicit-shared-verifier',
            ),
            pytest.param(
                ('verifier', 'environment_mode'),
                None,
                'set `[verifier] environment_mode = "separate"`.',
                id='unset-verifier-mode',
            ),
            pytest.param(
                ('verifier', 'environment'),
                {'cpus': 1},
                'remove `[verifier.environment]`, since the verifier uses the '
                "task's environment.",
                id='verifier-environment',
            ),
            pytest.param(
                ('steps',),
                [{'name': 'one'}],
                'remove `[[steps]]`, whose overrides the check cannot prove.',
                id='steps',
            ),
            pytest.param(
                ('artifacts',),
                None,
                'list `"/app"` under `[[artifacts]]`.',
                id='no-artifact',
            ),
            pytest.param(
                ('artifacts',),
                ['/logs/x'],
                'list `"/app"` under `[[artifacts]]`.',
                id='artifact-outside-app',
            ),
            pytest.param(
                ('artifacts',),
                [{'source': '/app', 'destination': 'workspace'}],
                'remove the `destination` of the `"/app"` artifact, since '
                'collection reads the workspace at its source path.',
                id='app-artifact-with-destination',
            ),
            pytest.param(
                ('environment', 'cpus'),
                None,
                'set `[environment] cpus`.',
                id='no-cpus',
            ),
            pytest.param(
                ('environment', 'memory_mb'),
                None,
                'set `[environment] memory_mb`.',
                id='no-memory',
            ),
            pytest.param(
                ('agent', 'timeout_sec'),
                None,
                'set `[agent] timeout_sec`.',
                id='no-agent-timeout',
            ),
        ],
    )
    def test_rejects_a_task_that_breaks_a_rule(
        self,
        families_dir: Path,
        path: tuple[str, ...],
        value: Any,
        message: str,
    ) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], path, value)

        with pytest.raises(ValueError) as error:
            check_tasks(family, tasks)

        assert f'`tasks/echo/task.toml`: {message}' in str(error.value)

    def test_accepts_a_string_artifact(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('artifacts',), ['/app'])

        check_tasks(family, tasks)

    def test_names_a_problem_in_a_later_task(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo', 'again')
        break_setting(tasks[1], ('environment', 'cpus'), None)

        with pytest.raises(ValueError) as error:
            check_tasks(family, tasks)

        assert str(error.value) == (
            'Family `f` has tasks to fix:\n'
            '`tasks/again/task.toml`: set `[environment] cpus`.'
        )

    def test_names_a_local_task(self, data_dir: Path) -> None:
        task = data_dir / 'cases' / 'smoke' / 'echo'
        task.mkdir(parents=True)
        config = tomllib.loads(
            (FAMILIES_DIR / 'smoke' / 'tasks' / 'echo' / 'task.toml').read_text()
        )
        del config['environment']['cpus']
        (task / 'task.toml').write_text(tomli_w.dumps(config))

        with pytest.raises(ValueError) as error:
            check_tasks(load_family('smoke'), [task], private_root=data_dir / 'cases')

        assert '`cases/smoke/echo/task.toml`: set `[environment] cpus`.' in str(
            error.value
        )

    def test_names_every_problem_of_a_task(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('environment', 'cpus'), None)
        break_setting(tasks[0], ('environment', 'memory_mb'), None)

        with pytest.raises(ValueError) as error:
            check_tasks(family, tasks)

        assert str(error.value) == (
            'Family `f` has tasks to fix:\n'
            '`tasks/echo/task.toml`: set `[environment] cpus`.\n'
            '`tasks/echo/task.toml`: set `[environment] memory_mb`.'
        )

    def test_rejects_a_task_toml_that_fails_to_validate(
        self, families_dir: Path
    ) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('environment', 'cpus'), 'two')

        with pytest.raises(ValueError) as error:
            check_tasks(family, tasks)

        assert str(error.value) == (
            'Family `f` has tasks to fix:\n`tasks/echo/task.toml`: '
            '`environment.cpus.int`: Input should be a valid integer, '
            'unable to parse string as an integer.\n'
            '`tasks/echo/task.toml`: `environment.cpus.float`: Input should be '
            'a valid number, unable to parse string as a number.'
        )


class TestAuthorVendors:
    def test_resolves_provenance_without_requiring_a_runnable_author(
        self, families_dir: Path, export: NixosConfigExport
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        task = case_task(family, 'echo')
        break_setting(task, ('metadata', 'author'), 'retired-author')
        break_setting(task, ('metadata', 'author_vendor'), 'acme')

        vendors = author_vendors(family, [task], export)

        assert vendors == {'echo': 'acme'}

    @pytest.mark.parametrize(
        ('field', 'value', 'message'),
        [
            ('author', None, '`author`: Field required'),
            ('author', '', '`author`: String should have at least 1 character'),
            ('author', 42, '`author`: Input should be a valid string'),
            ('author_vendor', None, '`author_vendor`: Field required'),
            (
                'author_vendor',
                '',
                '`author_vendor`: String should have at least 1 character',
            ),
            ('author_vendor', 42, '`author_vendor`: Input should be a valid string'),
            (
                'author_vendor',
                'unknown',
                'author vendor `unknown` is not one of `acme`, `other`.',
            ),
        ],
        ids=[
            'missing-author',
            'empty-author',
            'non-string-author',
            'missing-vendor',
            'empty-vendor',
            'non-string-vendor',
            'unknown-vendor',
        ],
    )
    def test_names_invalid_author_metadata(
        self,
        families_dir: Path,
        export: NixosConfigExport,
        field: str,
        value: str | int | None,
        message: str,
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        task = case_task(family, 'echo')
        break_setting(task, ('metadata', field), value)

        with pytest.raises(ValueError) as error:
            author_vendors(family, [task], export)

        assert str(error.value).startswith(
            f'Family `f` has authors to fix:\n`tasks/echo/task.toml`: {message}'
        )


class TestChangedTasks:
    @pytest.mark.parametrize(
        ('edit', 'version', 'expected'),
        [
            pytest.param('', '1.0.0', [], id='unedited'),
            pytest.param(
                '# Edited.\n', '1.0.0', [('echo', '1.0.0')], id='edited-without-a-bump'
            ),
            pytest.param('# Edited.\n', '1.0.1', [], id='edited-with-a-bump'),
        ],
    )
    def test_names_a_task_edited_under_a_version_a_trial_recorded(
        self,
        families_dir: Path,
        data_dir: Path,
        edit: str,
        version: str,
        expected: list[tuple[str, str]],
    ) -> None:
        family = voted_tasks(families_dir, 'again', 'echo')
        tasks = [case_task(family, task_id) for task_id in ('again', 'echo')]
        first = finish_job(
            jobs.job(
                family,
                jobs.family_systems(family, [make_system()]),
                tasks,
                root=data_dir,
                sources=[],
            )
        )
        retired = tasks[0] / 'tests' / 'test.sh'
        retired.write_text(retired.read_text() + '# Edited.\n')
        test = tasks[1] / 'tests' / 'test.sh'
        test.write_text(test.read_text() + edit)
        config = tasks[1] / 'task.toml'
        config.write_text(
            config.read_text().replace('version = "1.0.0"', f'version = "{version}"')
        )

        changed = changed_tasks(family, tasks[1:], [jobs.job_source(first)])

        assert changed == expected
