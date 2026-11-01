import asyncio
import os
import subprocess
import textwrap
from collections import defaultdict
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.task.config import TaskConfig
from harbor.models.trial.config import AgentConfig
from rich.text import Text
from typer.testing import CliRunner, Result

from assistant_bench import catalog, cli, jobs, manifest, report
from assistant_bench.arena import publication, votes
from assistant_bench.catalog import (
    EXPORT_VAR,
    NixosConfigExport,
    System,
    load_export,
    load_systems,
)
from assistant_bench.cli import app
from assistant_bench.families import Family, Gate, family_names, load_cases, load_family
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.endpoint import SDK_HEADERS_VAR
from assistant_bench.outputs import Output, write_outputs
from support import (
    REPORT_FIXTURE,
    SQLiteDatabase,
    arena_database,
    finish_job,
    make_output,
    make_system,
    passing_outputs,
    record_vote,
    write_case_family,
    write_task_family,
    write_trial,
    write_votes_family,
)


runner = CliRunner()

RENAME_GUIDE = (
    'Give a changed system a new name and keep the old name only in closed '
    'reports, as `docs/guide/authoring.md#systems` describes.'
)
DRIFTED = (
    'System `opus-5.5-high` ran under fingerprint `fp-old` but now resolves to '
    '`{current}`.'
)
MOVED = (
    'Closed reports of `f` froze `opus-5.5-high` under fingerprint `fp-old`, but it '
    'now resolves to `{current}`.'
)
RENAME = f'Closed reports name `opus-5.5-high`. {RENAME_GUIDE}'
STALE_TRIALS = (
    'Delete the stale trials of `opus-5.5-high` under `jobs/f/` in the data '
    'directory before the next run.'
)


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args), env={'COLUMNS': '200'})


def trials(
    case: str, system: str, reward: float | None, *, error: str | None = None
) -> list[Output]:
    """Return one output per check attempt for a task and agent."""
    return [
        make_output(case=case, system=system, reward=reward, error=error)
        for _ in range(jobs.CHECK_ATTEMPTS)
    ]


CLOSE_CASES = {'c1': 'reply', 'c2': 'docs'}


@dataclass(frozen=True)
class Store:
    database: SQLiteDatabase
    published: publication.Publication
    targets: list[str]
    """Each target a command connected to."""


def write_job(job_dir: Path) -> None:
    """Write the files that mark a finished Harbor job without trials."""
    job_dir.mkdir(parents=True)
    (job_dir / 'config.json').touch()
    (job_dir / 'result.json').touch()


def write_closed_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    frozen: str | None,
) -> None:
    """Close a round of the family that froze `opus-5.5-high` under a fingerprint."""
    monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
    closed = report.Report.model_validate_json(
        REPORT_FIXTURE.read_text().replace('acme-large', 'opus-5.5-high')
    )
    systems = closed.systems | {
        'opus-5.5-high': closed.systems['opus-5.5-high'].model_copy(
            update={'fingerprint': frozen}
        )
    }
    report.write_report(
        closed.model_copy(update={'family': family, 'systems': systems})
    )


@pytest.fixture
def submitted(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> list[JobConfig]:
    """Record each job `bench` submits in place of running Harbor."""
    configs: list[JobConfig] = []

    def run_job(job: JobConfig) -> Path:
        configs.append(job)
        return jobs.job_path(job)

    monkeypatch.setattr(jobs, 'run_job', run_job)
    return configs


class TestMain:
    def test_sets_the_environment_harbor_runs_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in jobs.HARBOR_ENV:
            monkeypatch.delenv(name)
        monkeypatch.setenv(SDK_HEADERS_VAR, 'x-assistant-header: assistant-value')

        result = invoke('check', 'smoke-text')
        assert result.exit_code == 0, result.output

        assert {name: os.environ.get(name) for name in jobs.HARBOR_ENV} == {
            'HARBOR_TELEMETRY': 'off',
            'LITELLM_MODE': 'PRODUCTION',
        }
        assert SDK_HEADERS_VAR not in os.environ


class TestCheck:
    def test_counts_the_cases_of_a_case_family(
        self, submitted: list[JobConfig]
    ) -> None:
        result = invoke('check', 'smoke-text')
        assert result.exit_code == 0, result.output

        assert result.output == 'Family `smoke-text`: 2 valid cases.\n'
        assert submitted == []

    def test_checks_every_family_without_a_name(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        write_case_family(families_dir, ['opus-5.5-high'], name='g')

        result = invoke('check')
        assert result.exit_code == 0, result.output

        assert result.output == (
            'Family `f`: 0 valid cases.\nFamily `g`: 0 valid cases.\n'
        )
        assert submitted == []

    def test_checks_a_named_family_once(self, families_dir: Path) -> None:
        write_case_family(families_dir, ['opus-5.5-high'])

        result = invoke('check', 'f', 'f')
        assert result.exit_code == 0, result.output

        assert result.output == 'Family `f`: 0 valid cases.\n'

    @pytest.mark.parametrize('reward', [1.0, 0.0], ids=['passes', 'fails'])
    def test_checks_only_named_tasks_once(
        self,
        families_dir: Path,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
        reward: float,
    ) -> None:
        write_task_family(families_dir, 'a', 'b', 'c')
        outputs = [
            *trials('b', 'oracle', reward),
            *trials('b', 'nop', 0.0),
            *trials('c', 'oracle', 1.0),
            *trials('c', 'nop', 0.0),
        ]

        def read_trials(
            family: Family, job_dir: Path, systems: dict[str, System]
        ) -> list[Output]:
            return outputs

        monkeypatch.setattr(jobs, 'read_trials', read_trials)

        result = invoke('check', 'f', 'f', '--task', 'c', '--task', 'b', '--task', 'b')
        assert result.exit_code == (0 if reward == 1.0 else 1), result.output

        (job,) = submitted
        assert [task.path for task in job.tasks] == [
            families_dir / 'f' / 'tasks' / 'b',
            families_dir / 'f' / 'tasks' / 'c',
        ]
        if reward == 1.0:
            assert 'failed every selected task.' in result.stdout
        else:
            assert result.stderr == (
                'Family `f`: the reference solution failed the tests of `b`.\n'
            )

    @pytest.mark.parametrize(
        ('arguments', 'message'),
        [
            (('--task', 'a'), 'Select exactly one task family with `--task`.'),
            (
                ('f', 'g', '--task', 'a'),
                'Select exactly one task family with `--task`.',
            ),
            (
                ('cases', '--task', 'a'),
                'Family `cases` has cases. Select a task family with `--task`.',
            ),
            (
                ('f', '--task', 'unknown'),
                'Family `f` has no task named `unknown`. Choose among `a`.',
            ),
        ],
        ids=['no-family', 'several-families', 'case-family', 'unknown-task'],
    )
    def test_rejects_invalid_task_selection_before_running(
        self,
        families_dir: Path,
        submitted: list[JobConfig],
        arguments: tuple[str, ...],
        message: str,
    ) -> None:
        write_task_family(families_dir, 'a', name='f')
        write_task_family(families_dir, 'a', name='g')
        write_case_family(families_dir, ['opus-5.5-high'], name='cases')

        result = invoke('check', *arguments)
        assert result.exit_code == 2, result.output

        assert message in Text.from_ansi(result.stderr).plain
        assert submitted == []

    @pytest.mark.parametrize(
        ('tasks', 'outputs', 'exit_code', 'message'),
        [
            pytest.param(
                ('a',),
                [*trials('a', 'oracle', 1.0), *trials('a', 'nop', 0.0)],
                0,
                'Family `f`: every reference solution passed its tests, and the '
                'do-nothing agent failed every task.\n',
                id='passed',
            ),
            pytest.param(
                ('a',),
                [*trials('a', 'oracle', 0.0), *trials('a', 'nop', 0.0)],
                1,
                'Family `f`: the reference solution failed the tests of `a`.\n',
                id='oracle-failed',
            ),
            pytest.param(
                ('a',),
                [*trials('a', 'nop', 0.0)],
                1,
                'Family `f`: the reference solution left no trial of `a`.\n',
                id='oracle-left-no-trial',
            ),
            pytest.param(
                ('a',),
                [*trials('a', 'oracle', 1.0), *trials('a', 'nop', 1.0)],
                1,
                'Family `f`: the do-nothing agent passed the tests of `a`.\n',
                id='nop-passed',
            ),
            pytest.param(
                ('a',),
                [*trials('a', 'oracle', 1.0)],
                1,
                'Family `f`: the do-nothing agent left no trial of `a`.\n',
                id='nop-left-no-trial',
            ),
            pytest.param(
                ('a',),
                [*trials('a', 'oracle', 1.0), *trials('a', 'nop', None)],
                1,
                'Family `f`: the do-nothing agent passed the tests of `a`.\n',
                id='nop-without-a-verdict',
            ),
            pytest.param(
                ('a',),
                [],
                1,
                'Family `f`: the reference solution left no trial of `a`.\n'
                'Family `f`: the do-nothing agent left no trial of `a`.\n',
                id='no-trials',
            ),
            pytest.param(
                ('a',),
                [
                    *trials('a', 'oracle', 1.0, error='TimeoutError'),
                    *trials('a', 'nop', 0.0),
                ],
                1,
                'Family `f`: the reference solution errored on `a` (`TimeoutError`).\n',
                id='oracle-errored',
            ),
            pytest.param(
                ('a',),
                [
                    *trials('a', 'oracle', 1.0),
                    *trials('a', 'nop', 0.0, error='VerifierTimeoutError'),
                ],
                1,
                'Family `f`: the do-nothing agent errored on `a` '
                '(`VerifierTimeoutError`).\n',
                id='nop-errored',
            ),
            pytest.param(
                ('a', 'b'),
                [
                    *trials('a', 'oracle', 0.0),
                    *trials('b', 'oracle', 0.0),
                    *trials('a', 'nop', 0.0),
                    *trials('b', 'nop', 0.0),
                ],
                1,
                'Family `f`: the reference solution failed the tests of `a`, `b`.\n',
                id='both-tasks-failed',
            ),
            pytest.param(
                ('a', 'b'),
                [
                    *trials('a', 'oracle', 0.0),
                    *trials('a', 'nop', 0.0),
                    *trials('b', 'nop', 0.0),
                ],
                1,
                'Family `f`: the reference solution failed the tests of `a`.\n'
                'Family `f`: the reference solution left no trial of `b`.\n',
                id='failed-and-missing',
            ),
        ],
    )
    def test_runs_the_oracle_and_the_nop_on_task_directories(
        self,
        families_dir: Path,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
        tasks: tuple[str, ...],
        outputs: list[Output],
        exit_code: int,
        message: str,
    ) -> None:
        write_task_family(families_dir, *tasks)

        def read_trials(
            family: Family, job_dir: Path, systems: dict[str, System]
        ) -> list[Output]:
            return outputs

        monkeypatch.setattr(jobs, 'read_trials', read_trials)
        verdicts = {0: (message, ''), 1: ('', message)}

        result = invoke('check', 'f')

        (job,) = submitted
        verdict, problems = verdicts[exit_code]
        assert (result.exit_code, result.stdout, result.stderr) == (
            exit_code,
            f'Job written to `{jobs.job_path(job)}`.\n{verdict}',
            problems,
        )
        assert [agent.name for agent in job.agents] == ['oracle', 'nop']
        assert job.n_attempts == jobs.CHECK_ATTEMPTS
        assert [task.path.name for task in job.tasks if task.path] == list(tasks)

    def test_refuses_a_task_without_an_author_before_running_harbor(
        self,
        families_dir: Path,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_task_family(families_dir, 'echo', systems=('large',))
        path = families_dir / 'f' / 'tasks' / 'echo' / 'task.toml'
        path.write_text(path.read_text().replace('author = "large"\n', ''))
        monkeypatch.setattr(
            cli, '_load_systems', lambda: {'large': make_system(name='large')}
        )

        result = invoke('check', 'f')

        assert result.exit_code == 1
        assert '`tasks/echo/task.toml`: `author`: Field required.' in result.output
        assert submitted == []

    def test_fails_when_an_earlier_task_family_fails_and_a_later_one_passes(
        self,
        families_dir: Path,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_task_family(families_dir, 'a', name='f')
        write_task_family(families_dir, 'a', name='g')
        outputs = {
            'f': [*trials('a', 'oracle', 0.0), *trials('a', 'nop', 0.0)],
            'g': [*trials('a', 'oracle', 1.0), *trials('a', 'nop', 0.0)],
        }

        def read_trials(
            family: Family, job_dir: Path, systems: dict[str, System]
        ) -> list[Output]:
            return outputs[family.name]

        monkeypatch.setattr(jobs, 'read_trials', read_trials)

        result = invoke('check')

        assert result.exit_code == 1
        assert [job.jobs_dir.name for job in submitted] == ['f', 'g']
        assert result.stderr == (
            'Family `f`: the reference solution failed the tests of `a`.\n'
        )
        assert (
            'Family `g`: every reference solution passed its tests, and the '
            'do-nothing agent failed every task.\n'
        ) in result.stdout

    def test_validates_every_family_before_running_a_check(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_task_family(families_dir, 'a', name='f')
        write_case_family(families_dir, ['ghost'], name='g')

        result = invoke('check')

        assert result.exit_code == 1
        assert 'Family `g` lists `ghost`' in result.stderr
        assert submitted == []

    @pytest.mark.parametrize(
        ('old', 'new', 'message'),
        [
            pytest.param(
                'network_mode = "no-network"',
                'network_mode = "public"',
                '`tasks/echo/task.toml`: the agent phase needs `no-network`.',
                id='open-network',
            ),
            pytest.param(
                'name = "smoke/echo"',
                'name = "smoke/other"',
                'Family `smoke`: set `[task] name` to `"smoke/echo"` in '
                '`tasks/echo/task.toml`.',
                id='name-apart-from-its-directory',
            ),
        ],
    )
    def test_rejects_a_task_that_breaks_a_rule(
        self,
        families_dir: Path,
        submitted: list[JobConfig],
        old: str,
        new: str,
        message: str,
    ) -> None:
        write_task_family(families_dir, 'echo', name='smoke')
        config = families_dir / 'smoke' / 'tasks' / 'echo' / 'task.toml'
        config.write_text(config.read_text().replace(old, new))

        result = invoke('check', 'smoke')

        assert result.exit_code == 1
        assert message in result.stderr
        assert 'Usage:' not in result.output
        assert submitted == []

    @pytest.mark.parametrize(
        ('started', 'message'),
        [
            pytest.param(
                True,
                'Harbor exited with code 5. Browse its trials with '
                '`harbor view {job}`.\n',
                id='job-started',
            ),
            pytest.param(False, 'Harbor exited with code 5.\n', id='preflight-failed'),
        ],
    )
    def test_reports_a_failed_harbor_run(
        self, monkeypatch: pytest.MonkeyPatch, started: bool, message: str
    ) -> None:
        failed: list[JobConfig] = []

        def run_job(job: JobConfig) -> Path:
            failed.append(job)
            if started:
                jobs.job_path(job).mkdir(parents=True)
            raise subprocess.CalledProcessError(5, 'harbor')

        monkeypatch.setattr(jobs, 'run_job', run_job)

        result = invoke('check', 'smoke')

        (job,) = failed
        assert (result.exit_code, result.stdout, result.stderr) == (
            5,
            '',
            message.format(job=jobs.job_path(job)),
        )

    def test_reports_an_unreadable_check_trial_without_a_traceback(
        self, submitted: list[JobConfig], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def read_trials(family: Any, job_dir: Path, systems: Any) -> list[Output]:
            raise ValueError('Trial `echo__1` has an unreadable `result.json`.')

        monkeypatch.setattr(jobs, 'read_trials', read_trials)

        result = invoke('check', 'smoke')

        assert (result.exit_code, result.stderr) == (
            1,
            'Trial `echo__1` has an unreadable `result.json`.\n',
        )

    def test_reports_an_unreadable_run_trial_without_a_traceback(
        self, data_dir: Path, submitted: list[JobConfig]
    ) -> None:
        job = data_dir / 'jobs' / 'smoke-text' / 'job-1'
        write_trial(
            job,
            'en-greeting__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 'opus-5.5-high'}),
            task='smoke-text/en-greeting',
        )
        (job / 'en-greeting__1' / 'lock.json').unlink()

        result = invoke('check', 'smoke-text')

        assert result.exit_code == 1
        assert 'has a result.json but no lock.json' in result.stderr
        assert submitted == []

    def test_names_the_file_and_field_of_an_invalid_family(
        self, families_dir: Path
    ) -> None:
        config = write_case_family(families_dir, ['opus-5.5-high']).with_name(
            'family.toml'
        )
        config.write_text(config.read_text().replace('samples = 1', 'samples = 0'))

        result = invoke('check', 'f')

        assert (result.exit_code, result.output) == (
            1,
            'Family `f`: `family.toml`: `samples`: Input should be greater than or '
            'equal to 1.\n',
        )

    def test_rejects_a_local_case_that_reuses_a_committed_id(
        self, data_dir: Path
    ) -> None:
        local = data_dir / 'cases' / 'smoke-text.toml'
        local.parent.mkdir(parents=True)
        local.write_text(
            tomli_w.dumps(
                {
                    'cases': [
                        {
                            'id': 'en-greeting',
                            'category': 'message',
                            'source': 'held-out',
                            'instruction': 'Hi.',
                        }
                    ]
                }
            )
        )

        result = invoke('check', 'smoke-text')

        assert result.exit_code == 1
        assert 'case `en-greeting` appears at' in result.output

    def test_rejects_a_family_whose_harness_cannot_run_a_listed_system(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_task_family(
            families_dir, 'echo', systems=('opus-5.5-high',), author_vendor='anthropic'
        )
        config = families_dir / 'f' / 'family.toml'
        config.write_text(config.read_text().replace('terminus-2', 'claude-code'))

        result = invoke('check', 'f')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `f`: harness `claude-code` is not one of `generate`, '
            '`terminus-2`.\n',
        )
        assert submitted == []

    def test_rejects_a_family_whose_harness_cannot_send_a_listed_prompt(
        self,
        families_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        submitted: list[JobConfig],
    ) -> None:
        write_task_family(
            families_dir, 'echo', systems=('opus-5.5-high',), author_vendor='anthropic'
        )
        systems = load_systems(load_export())
        prompted = systems['opus-5.5-high'].model_copy(update={'prompt': 'Be brief.'})

        def prompted_systems(export: NixosConfigExport) -> dict[str, System]:
            return systems | {'opus-5.5-high': prompted}

        monkeypatch.setattr(cli, 'load_systems', prompted_systems)

        result = invoke('check', 'f')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `f` lists `opus-5.5-high`, whose prompt the `terminus-2` harness '
            'cannot send. Remove them from `systems`.\n',
        )
        assert submitted == []

    def test_rejects_a_family_listing_an_unknown_system(
        self, families_dir: Path
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high', 'ghost'])

        result = invoke('check', 'f')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `f` lists `ghost`, which `systems.toml` does not define. Fix '
            '`families/f/family.toml` or add the system table.\n',
        )

    def test_rejects_a_systems_table_that_fails_to_validate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / 'systems.toml'
        path.write_text('[broken]\neffort = "low"\n')

        def invalid_systems(export: NixosConfigExport) -> dict[str, System]:
            return catalog.load_systems(export, path)

        monkeypatch.setattr(cli, 'load_systems', invalid_systems)

        result = invoke('check', 'smoke-text')

        assert (result.exit_code, result.output) == (
            1,
            '`systems.toml`: `broken`: set exactly one of `model` and `route`.\n',
        )

    def test_reports_a_missing_export_without_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(EXPORT_VAR)

        result = invoke('check', 'smoke-text')

        assert (result.exit_code, result.output) == (
            1,
            '`BENCH_NIXOS_CONFIG` is unset. Run `bench` inside `nix develop`.\n',
        )


class TestKnownFamilies:
    @pytest.mark.parametrize('command', ['check', 'run', 'collect', 'close'])
    def test_rejects_a_missing_family_and_names_the_choices(self, command: str) -> None:
        choices = ', '.join(f'`{name}`' for name in family_names())

        result = invoke(command, 'nope')

        assert result.exit_code == 2
        assert f'No family named `nope`. Choose among {choices}.' in result.output


class TestRun:
    def test_rejects_repeated_systems_before_submitting_a_job(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high', 'opus-5.5-high'])

        result = invoke('run', 'f')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `f`: `family.toml`: `systems`: repeats `opus-5.5-high`.\n',
        )
        assert submitted == []

    def test_generates_the_listed_systems_on_every_case(
        self, submitted: list[JobConfig]
    ) -> None:
        result = invoke('run', 'smoke-text')
        assert result.exit_code == 0, result.output

        family = load_family('smoke-text')
        (job,) = submitted
        assert [agent.kwargs['system'] for agent in job.agents] == (
            family.settings.systems
        )
        assert [task.path.name for task in job.tasks if task.path] == [
            case.id for case in load_cases(family)
        ]

    def test_generates_only_the_named_systems_in_family_order(
        self, submitted: list[JobConfig]
    ) -> None:
        result = invoke(
            'run', 'smoke-text', '--system', 'kimi-k3-high', '--system', 'opus-5.5-high'
        )
        assert result.exit_code == 0, result.output

        (job,) = submitted
        assert [agent.kwargs['system'] for agent in job.agents] == [
            'opus-5.5-high',
            'kimi-k3-high',
        ]

    def test_rejects_a_named_system_the_family_does_not_list(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'])

        result = invoke(
            'run', 'f', '--system', 'gpt-6-astra-high', '--system', 'opus-5.5-high'
        )

        assert result.exit_code == 2
        assert (
            'Family `f` does not list `gpt-6-astra-high`. Choose among `opus-5.5-high`.'
            in result.output
        )
        assert submitted == []

    def test_generates_after_a_named_run_only_the_systems_it_left_out(
        self, submitted: list[JobConfig]
    ) -> None:
        named = ['gpt-6.1-sol-high', 'kimi-k3-high']
        args = [arg for name in named for arg in ('--system', name)]
        result = invoke('run', 'smoke-text', *args)
        assert result.exit_code == 0, result.output
        first = finish_job(submitted[0])

        result = invoke('run', 'smoke-text')
        assert result.exit_code == 0, result.output

        job = submitted[1]
        plan = asyncio.run(JobPlan.from_config(job))
        (source,) = job.source_jobs
        assert source.path == first
        diff = compare_job_trials(plan, load_source_trials([(source, first)]))
        actions: defaultdict[str, set[DiffAction]] = defaultdict(set)
        for trial, trial_diff in zip(plan.trial_configs, diff.trials, strict=True):
            actions[trial.agent.kwargs['system']].add(trial_diff.action)
        assert actions == {
            system: {DiffAction.REUSE if system in named else DiffAction.RERUN}
            for system in load_family('smoke-text').settings.systems
        }

    def test_runs_a_task_family_under_its_harness(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_task_family(
            families_dir, 'echo', systems=('opus-5.5-high',), author_vendor='anthropic'
        )

        result = invoke('run', 'f')
        assert result.exit_code == 0, result.output

        (job,) = submitted
        (agent,) = job.agents
        opus = load_systems(load_export())['opus-5.5-high']
        assert agent.import_path == 'assistant_bench.harnesses.terminus:Terminus'
        assert agent.model_name == f'openai/{opus.route}'
        assert agent.kwargs['fingerprint'] == fingerprint(
            opus.model_copy(update={'harness': 'terminus-2'})
        )
        assert 'max_turns' not in agent.kwargs
        assert agent.kwargs['api'] == 'anthropic-messages'
        assert agent.kwargs['thinking_mode'] == 'anthropic-adaptive'
        assert agent.kwargs['reasoning_effort'] == 'high'
        assert 'llm_call_kwargs' not in agent.kwargs
        assert agent.kwargs['model_info'] == {
            'max_input_tokens': opus.context_window - opus.max_output_tokens,
            'max_output_tokens': opus.max_output_tokens,
        }
        assert job.n_concurrent_trials == jobs.N_CONCURRENT_CONTAINERS
        assert not job.verifier.disable
        assert [task.path for task in job.tasks] == [
            families_dir / 'f' / 'tasks' / 'echo'
        ]

    def test_runs_after_a_task_family_run_only_the_systems_it_left_out(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_task_family(
            families_dir,
            'echo',
            systems=('opus-5.5-high', 'kimi-k3-high'),
            author_vendor='anthropic',
        )
        result = invoke('run', 'f', '--system', 'opus-5.5-high')
        assert result.exit_code == 0, result.output
        first = finish_job(submitted[0])

        result = invoke('run', 'f')
        assert result.exit_code == 0, result.output

        job = submitted[1]
        plan = asyncio.run(JobPlan.from_config(job))
        diff = compare_job_trials(
            plan, load_source_trials([(job.source_jobs[0], first)])
        )
        assert {
            trial.agent.kwargs['system']: trial_diff.action
            for trial, trial_diff in zip(plan.trial_configs, diff.trials, strict=True)
        } == {'opus-5.5-high': DiffAction.REUSE, 'kimi-k3-high': DiffAction.RERUN}

    def test_rejects_a_named_family_without_systems(
        self, submitted: list[JobConfig]
    ) -> None:
        result = invoke('run', 'smoke')

        assert (result.exit_code, result.output) == (
            1,
            'Family `smoke` lists no systems. Add them to `systems` in '
            '`families/smoke/family.toml`.\n',
        )
        assert submitted == []

    def test_generates_every_family_that_lists_systems(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        write_case_family(families_dir, ['opus-5.5-high', 'kimi-k3-high'], name='g')
        write_task_family(families_dir, name='z')

        result = invoke('run')
        assert result.exit_code == 0, result.output

        assert [job.jobs_dir.name for job in submitted] == ['f', 'g']
        assert [len(job.agents) for job in submitted] == [1, 2]

    def test_generates_a_named_family_once(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'])

        result = invoke('run', 'f', 'f')
        assert result.exit_code == 0, result.output

        (job,) = submitted
        assert job.jobs_dir.name == 'f'

    def test_generates_a_named_system_on_every_family_that_lists_it(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        write_case_family(families_dir, ['opus-5.5-high', 'kimi-k3-high'], name='g')

        result = invoke('run', '--system', 'kimi-k3-high')
        assert result.exit_code == 0, result.output

        (job,) = submitted
        assert job.jobs_dir.name == 'g'
        assert [agent.kwargs['system'] for agent in job.agents] == ['kimi-k3-high']

    def test_rejects_a_named_system_no_family_lists(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')

        result = invoke('run', '--system', 'gpt-6-astra-high')

        assert result.exit_code == 2
        assert (
            'No family lists `gpt-6-astra-high` in its `systems`. Check the name '
            'against `systems.toml`.' in result.output
        )
        assert submitted == []

    def test_collects_the_run_into_outputs(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def run_job(job: JobConfig) -> Path:
            write_trial(
                jobs.job_path(job),
                'en-greeting__1',
                job.agents[0],
                task='smoke-text/en-greeting',
                output='Hi.',
                instruction=None,
                context=AgentContext(metadata={'finish_reason': 'stop'}),
                environment=job.environment,
                verifier_disabled=job.verifier.disable,
            )
            return jobs.job_path(job)

        monkeypatch.setattr(jobs, 'run_job', run_job)

        result = invoke('run', 'smoke-text')
        assert result.exit_code == 0, result.output

        assert 'Family `smoke-text`: 1 outputs, 0 errored.\n' in result.output
        written = (data_dir / 'outputs' / 'smoke-text.jsonl').read_text()
        assert written.count('\n') == 1

    def test_stops_after_a_failed_family(
        self,
        families_dir: Path,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        write_case_family(families_dir, ['opus-5.5-high'], name='g')
        submitted: list[JobConfig] = []

        def run_job(job: JobConfig) -> Path:
            submitted.append(job)
            raise subprocess.CalledProcessError(3, 'harbor')

        monkeypatch.setattr(jobs, 'run_job', run_job)

        result = invoke('run')

        assert result.exit_code == 3
        assert [job.jobs_dir.name for job in submitted] == ['f']

    def test_collects_finished_trials_and_names_the_job_when_harbor_fails(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        submitted: list[JobConfig] = []

        def run_job(job: JobConfig) -> Path:
            submitted.append(job)
            write_trial(
                jobs.job_path(job),
                'en-greeting__1',
                job.agents[0],
                task='smoke-text/en-greeting',
                output='Hi.',
                instruction=None,
                context=AgentContext(
                    n_input_tokens=10,
                    n_output_tokens=2,
                    metadata={'finish_reason': 'stop'},
                ),
                environment=job.environment,
                verifier_disabled=job.verifier.disable,
            )
            raise subprocess.CalledProcessError(3, 'harbor')

        monkeypatch.setattr(jobs, 'run_job', run_job)

        result = invoke('run', 'smoke-text')

        (job,) = submitted
        assert (result.exit_code, result.stdout, result.stderr) == (
            3,
            'Family `smoke-text`: 1 outputs, 0 errored.\n',
            'Harbor exited with code 3. Browse its trials with '
            f'`harbor view {jobs.job_path(job)}`.\n',
        )
        written = (data_dir / 'outputs' / 'smoke-text.jsonl').read_text()
        assert written.count('\n') == 1

    @pytest.mark.parametrize(
        ('reported', 'frozen', 'trial', 'expected'),
        [
            pytest.param(
                False,
                None,
                True,
                [DRIFTED, STALE_TRIALS],
                id='unreported',
            ),
            pytest.param(
                True,
                'fp-old',
                True,
                [MOVED, DRIFTED, RENAME],
                id='moved',
            ),
            pytest.param(
                True,
                'fp-old',
                False,
                [MOVED, RENAME],
                id='moved-without-trials',
            ),
            pytest.param(
                True,
                '{current}',
                True,
                [DRIFTED, STALE_TRIALS],
                id='restored',
            ),
            pytest.param(
                True,
                None,
                True,
                [DRIFTED, RENAME],
                id='unrecorded',
            ),
        ],
    )
    def test_rejects_a_system_whose_fingerprint_changed(
        self,
        tmp_path: Path,
        families_dir: Path,
        data_dir: Path,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
        reported: bool,
        frozen: str | None,
        trial: bool,
        expected: list[str],
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        current = fingerprint(load_systems(load_export())['opus-5.5-high'])
        if reported:
            write_closed_report(
                tmp_path,
                monkeypatch,
                'f',
                None if frozen is None else frozen.format(current=current),
            )
        if trial:
            write_trial(
                data_dir / 'jobs' / 'f' / '2026-10-20__10-00-00',
                'c1__1',
                AgentConfig(
                    import_path='x:Y',
                    kwargs={'system': 'opus-5.5-high', 'fingerprint': 'fp-old'},
                ),
                task='f/c1',
            )

        result = invoke('run', 'f')

        assert (result.exit_code, result.output) == (
            1,
            'Family `f` has systems that changed their fingerprints:\n'
            + ''.join(f'{line.format(current=current)}\n' for line in expected),
        )
        assert submitted == []

    def test_refuses_a_later_family_before_running_any(
        self, families_dir: Path, data_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        cases = write_case_family(families_dir, ['opus-5.5-high'], name='g')
        cases.write_text(
            tomli_w.dumps(
                {
                    'cases': [
                        {
                            'id': 'c1',
                            'category': 'message',
                            'source': 'synthetic',
                            'instruction': 'Hi.',
                        }
                    ]
                }
            )
        )
        write_trial(
            data_dir / 'jobs' / 'g' / '2026-10-20__10-00-00',
            'old__1',
            AgentConfig(
                import_path='x:Y',
                kwargs={'system': 'opus-5.5-high', 'fingerprint': 'fp-old'},
            ),
            task='g/old',
        )

        result = invoke('run')

        assert result.exit_code == 1
        assert 'System `opus-5.5-high` ran under fingerprint `fp-old`' in result.output
        assert submitted == []
        assert not (data_dir / 'tasks' / 'g' / 'c1').exists()

    def test_refuses_a_task_edited_without_a_bump_before_running_any(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        write_task_family(
            families_dir,
            'echo',
            name='z',
            systems=('opus-5.5-high',),
            author_vendor='anthropic',
        )
        result = invoke('run', 'z')
        assert result.exit_code == 0, result.output
        finish_job(submitted.pop())
        test = families_dir / 'z' / 'tasks' / 'echo' / 'tests' / 'test.sh'
        test.write_text(test.read_text() + '# Edited.\n')

        result = invoke('run')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `z` has tasks to bump:\n'
            'Task `echo` changed since a trial ran it at version `1.0.0`.\n'
            "Bump each task's `[task] version`: the patch for a change no trial\n"
            'depends on, such as its solution, the minor for a change to `tests/`\n'
            'alone, which regrades its trials, or the major for anything the agent\n'
            'sees, which reruns them.\n',
        )
        assert submitted == []

    def test_refuses_a_case_whose_rendered_task_changed_under_its_version(
        self, monkeypatch: pytest.MonkeyPatch, submitted: list[JobConfig]
    ) -> None:
        result = invoke('run', 'smoke-text')
        assert result.exit_code == 0, result.output
        finish_job(submitted.pop())
        render = jobs.render_task_config

        def with_new_default(name: str, instruction: str) -> TaskConfig:
            config = render(name, instruction)
            verifier = config.verifier.model_copy(update={'timeout_sec': 1.0})
            return config.model_copy(update={'verifier': verifier})

        monkeypatch.setattr(jobs, 'render_task_config', with_new_default)

        result = invoke('run', 'smoke-text')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `smoke-text` has cases whose rendered task changed under a '
            'version a trial recorded: `en-greeting`, `zh-greeting`. A Harbor '
            'upgrade that changes a `task.toml` default does this, and Harbor '
            "aborts the job until the family's earlier job directories are "
            'removed, as `docs/research/harbor.md#quirks-of-the-pinned-release` '
            'describes.\n',
        )
        assert submitted == []

    def test_rejects_an_unreadable_trial_record(
        self, families_dir: Path, data_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'], name='f')
        job = data_dir / 'jobs' / 'f' / '2026-10-20__10-00-00'
        write_trial(
            job,
            'c1__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 'opus-5.5-high'}),
            task='f/c1',
        )
        (job / 'c1__1' / 'lock.json').unlink()

        result = invoke('run', 'f')

        assert result.exit_code == 1
        assert 'has a result.json but no lock.json' in result.output
        assert submitted == []


class TestCollect:
    def test_reports_unknown_usage_and_its_cost_lower_bound(
        self,
        families_dir: Path,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cases = write_case_family(families_dir, ['large'], name='f')
        cases.write_text(
            textwrap.dedent(
                """\
                [[cases]]
                id = "c"
                category = "message"
                source = "synthetic"
                instruction = "Hi."
                """
            )
        )
        system = make_system(name='large')
        monkeypatch.setattr(cli, '_load_systems', lambda: {'large': system})
        config = jobs.job(load_family('f'), [system], [])
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'c__1',
            config.agents[0],
            task='f/c',
            output='Hi.',
            context=AgentContext(
                metadata={
                    'responses': [
                        {
                            'input_tokens': 1_000_000,
                            'cache_tokens': 0,
                            'cache_write_tokens': 0,
                            'output_tokens': 100_000,
                        },
                        {},
                    ]
                }
            ),
            environment=config.environment,
            verifier_disabled=config.verifier.disable,
        )

        result = invoke('collect', 'f')
        assert result.exit_code == 0, result.output

        assert (
            'System `large` has unknown usage. Outputs affected: 1. Responses '
            'without valid usage: 1. Collected outputs cost at least $3.00 '
            '(reported usage only).\n'
        ) in result.output
        (output,) = [
            Output.model_validate_json(line)
            for line in (data_dir / 'outputs' / 'f.jsonl').read_text().splitlines()
        ]
        assert output.cost_usd is None
        assert output.input_tokens is None

    def test_reports_an_unknown_task_author_vendor_without_a_traceback(
        self, families_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_task_family(families_dir, 'echo', systems=('large',))
        path = families_dir / 'f' / 'tasks' / 'echo' / 'task.toml'
        path.write_text(
            path.read_text().replace(
                'author_vendor = "acme"', 'author_vendor = "unknown"'
            )
        )
        monkeypatch.setattr(
            cli, '_load_systems', lambda: {'large': make_system(name='large')}
        )

        def collect(family: Family, systems: dict[str, System]) -> list[Output]:
            return []

        monkeypatch.setattr(jobs, 'collect', collect)

        result = invoke('collect', 'f')

        assert result.exit_code == 1
        assert (
            '`tasks/echo/task.toml`: author vendor `unknown` is not one of '
        ) in result.stderr
        assert 'Traceback' not in result.output

    def test_reports_each_vendor_pass_rate_with_authors_outside_the_family(
        self, families_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_task_family(
            families_dir,
            'own-a',
            'own-b',
            'other-a',
            'other-b',
            systems=('large', 'small'),
        )
        systems = {
            'large': make_system(name='large', vendor='acme'),
            'small': make_system(name='small', vendor='other'),
            'writer': make_system(name='writer', vendor='other'),
        }
        for name in ('other-a', 'other-b'):
            path = families_dir / 'f' / 'tasks' / name / 'task.toml'
            path.write_text(
                path.read_text()
                .replace('author = "large"', 'author = "writer"')
                .replace('author_vendor = "acme"', 'author_vendor = "other"')
            )
        outputs = [
            make_output(
                case=case,
                system=system,
                input_tokens=10,
                output_tokens=20,
                reward=float(case.startswith('own') == (system == 'large')),
            )
            for case in ('own-a', 'own-b', 'other-a', 'other-b')
            for system in ('large', 'small')
        ]
        monkeypatch.setattr(cli, '_load_systems', lambda: systems)

        def collect(family: Family, systems: dict[str, System]) -> list[Output]:
            return outputs

        monkeypatch.setattr(jobs, 'collect', collect)

        result = invoke('collect', 'f')
        assert result.exit_code == 0, result.output

        assert (
            'System `large`: pass rate on own-vendor tasks 2/2 (100.0%), '
            'other-vendor tasks 0/2 (0.0%).\n'
        ) in result.output
        assert (
            'System `small`: pass rate on own-vendor tasks 2/2 (100.0%), '
            'other-vendor tasks 0/2 (0.0%).\n'
        ) in result.output

    @pytest.mark.parametrize('reward', [0.0, None], ids=['failed', 'ungraded'])
    def test_names_missing_vendor_groups_and_counts_failures(
        self,
        families_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        reward: float | None,
    ) -> None:
        write_task_family(families_dir, 'one', systems=('large',))
        monkeypatch.setattr(
            cli, '_load_systems', lambda: {'large': make_system(name='large')}
        )

        def collect(family: Family, systems: dict[str, System]) -> list[Output]:
            return [
                make_output(
                    case='one',
                    system='large',
                    reward=reward,
                    input_tokens=10,
                    output_tokens=20,
                )
            ]

        monkeypatch.setattr(jobs, 'collect', collect)

        result = invoke('collect', 'f')
        assert result.exit_code == 0, result.output

        assert (
            'System `large`: pass rate on own-vendor tasks 0/1 (0.0%), '
            'other-vendor tasks no trials.\n'
        ) in result.output

    @pytest.mark.parametrize(
        ('name', 'outputs', 'summary'),
        [
            pytest.param(
                'smoke-text',
                [
                    make_output(gate_failures=[]),
                    make_output(gate_failures=[Gate.TRUNCATED, Gate.LENGTH]),
                    make_output(gate_failures=[Gate.TRUNCATED]),
                ],
                """\
                Family `smoke-text`: 3 outputs, 0 errored.
                2 failed a gate.
                  truncated: 2
                  length: 1
                """,
                id='gate-failures',
            ),
            pytest.param(
                'smoke',
                [
                    make_output(reward=1.0, harness_version='0.24.0'),
                    make_output(reward=0.0, harness_version='0.24.0'),
                    make_output(error='TimeoutError'),
                ],
                """\
                Family `smoke`: 3 outputs, 1 errored.
                  TimeoutError: 1
                Mean reward 0.500.
                """,
                id='errors-rewards-and-one-harbor-version',
            ),
            pytest.param(
                'smoke',
                [
                    make_output(case='a', reward=1.0, harness_version='0.24.0'),
                    make_output(case='b', reward=0.0, harness_version='0.25.0'),
                    make_output(case='c', reward=0.5, harness_version='0.24.0'),
                    make_output(case='d', error='RuntimeError'),
                ],
                """\
                Family `smoke`: 4 outputs, 1 errored.
                  RuntimeError: 1
                Mean reward 0.500.
                No system solved `b`, `c`.
                The outputs span Harbor versions `0.24.0`, `0.25.0`.
                """,
                id='unsolved-tasks-and-harbor-versions',
            ),
        ],
    )
    def test_summarizes_the_collected_outputs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        outputs: list[Output],
        summary: str,
    ) -> None:
        def collect(family: Family, systems: dict[str, System]) -> list[Output]:
            return outputs

        monkeypatch.setattr(jobs, 'collect', collect)

        result = invoke('collect', name)
        assert result.exit_code == 0, result.output

        assert result.output == textwrap.dedent(summary)

    def test_collects_every_family_with_jobs(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        for name in ('f', 'g', 'h'):
            write_case_family(families_dir, ['opus-5.5-high'], name=name)
        for name in ('f', 'g'):
            write_job(data_dir / 'jobs' / name / 'job-1')

        result = invoke('collect')
        assert result.exit_code == 0, result.output

        assert result.output == (
            'Family `f`: 0 outputs, 0 errored.\nFamily `g`: 0 outputs, 0 errored.\n'
        )
        assert (data_dir / 'outputs' / 'f.jsonl').exists()
        assert (data_dir / 'outputs' / 'g.jsonl').exists()
        assert not (data_dir / 'outputs' / 'h.jsonl').exists()

    def test_reports_when_no_family_has_jobs(self) -> None:
        result = invoke('collect')

        assert (result.exit_code, result.output) == (
            0,
            'No family has jobs yet. Run `uv run bench run` first.\n',
        )

    def test_reports_an_unreadable_trial_without_a_traceback(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_case_family(families_dir, ['opus-5.5-high'])
        trial = data_dir / 'jobs' / 'f' / 'job-1' / 'c1__1'
        write_trial(
            trial.parent,
            trial.name,
            AgentConfig(import_path='x:Y', kwargs={'system': 'opus-5.5-high'}),
            task='f/c1',
        )
        (trial / 'result.json').write_text('{')

        result = invoke('collect', 'f')

        assert result.exit_code == 1
        assert result.output.startswith(
            f"Source trial '{trial.resolve()}' has an unreadable result.json"
        )


class TestClose:
    @pytest.fixture
    def store(
        self, families_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Store:
        """Publish family `f` to a store that serves every target."""
        write_votes_family(
            families_dir, 'f', ['large', 'small'], CLOSE_CASES, public_voting=True
        )
        family = load_family('f')
        outputs = list(
            passing_outputs(CLOSE_CASES, ['large', 'small'], cost_usd=0.5).values()
        )
        write_outputs('f', outputs)
        published = publication.prepare(
            family,
            load_cases(family),
            outputs,
            {'large': 'Public Large', 'small': 'Public Small'},
            'production',
        )
        store = Store(arena_database(), published, [])
        publication.publish(store.database, published, datetime.now(UTC))

        def connect(target: str) -> AbstractContextManager[SQLiteDatabase]:
            store.targets.append(target)
            return nullcontext(store.database)

        monkeypatch.setattr(cli, 'connect', connect)
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        return store

    def test_saves_the_production_cut_and_closes_from_it(
        self,
        store: Store,
        data_dir: Path,
        tmp_path: Path,
    ) -> None:
        now = datetime.now(UTC)
        for case, actor in enumerate(('first', 'second')):
            record_vote(
                store.database,
                store.published,
                now,
                actor=actor,
                age=timedelta(hours=1),
                case=case,
            )
        last = record_vote(
            store.database, store.published, now, actor='third', age=timedelta(hours=1)
        )
        record_vote(
            store.database, store.published, now, actor='late', age=timedelta(minutes=1)
        )

        result = invoke('close', 'f')

        assert result.exit_code == 0, result.output
        assert store.targets == ['production']
        (snapshot,) = (data_dir / 'votes' / 'f').glob('*.json')
        assert f'Votes saved to `{snapshot}`.' in result.output
        (path,) = (tmp_path / 'reports' / 'f').iterdir()
        closed = report.Report.model_validate_json(path.read_text())
        assert closed.results.scorer == 'votes'
        assert closed.results.cutoffs == report.Cutoffs(
            production=report.Cutoff(id=last, votes=3), local=None
        )
        assert (closed.results.votes, closed.results.voters) == (3, 3)
        assert closed.systems['large'] == report.PublicSystem(
            display_name='Public Large', fingerprint='fp-large'
        )
        assert closed.results.standings[0].mean_cost_usd == 0.5

    def test_recloses_a_saved_snapshot_without_reading_the_store(
        self,
        store: Store,
        tmp_path: Path,
    ) -> None:
        now = datetime.now(UTC)
        record_vote(
            store.database, store.published, now, actor='a', age=timedelta(hours=1)
        )
        cut = votes.read_cut(store.database, 'f', now)
        path = votes.write_snapshot(
            votes.Snapshot(family='f', taken_at=now, production=cut, local=None)
        )

        result = invoke('close', 'f', '--snapshot', str(path))

        assert result.exit_code == 0, result.output
        assert store.targets == []
        assert (tmp_path / 'reports' / 'f').is_dir()

    def test_names_the_publish_command_when_production_has_no_publication(
        self, store: Store
    ) -> None:
        store.database.execute('DELETE FROM active')

        result = invoke('close', 'f')

        assert result.exit_code == 1
        assert result.stderr == (
            'Family `f` has no active publication in production, so it has no votes '
            'to close. Publish one with `uv run bench arena publish production '
            '<file>`.\n'
        )

    def test_refuses_votes_of_a_family_without_public_voting(
        self, families_dir: Path
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], CLOSE_CASES)

        result = invoke('close', 'f')

        assert result.exit_code == 1
        assert result.stderr == (
            'Family `f` sets no `public_voting`, so no store holds its votes.\n'
        )

    @pytest.mark.parametrize('exists', [False, True], ids=['missing', 'malformed'])
    def test_rejects_an_unreadable_snapshot_without_a_traceback(
        self, families_dir: Path, tmp_path: Path, exists: bool
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], CLOSE_CASES)
        snapshot = tmp_path / 'snapshot.json'
        if exists:
            snapshot.write_text('{')

        result = invoke('close', 'f', '--snapshot', str(snapshot))

        assert result.exit_code == 1
        assert 'Report written' not in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_refuses_a_snapshot_for_a_tests_family(
        self, families_dir: Path, tmp_path: Path
    ) -> None:
        write_task_family(families_dir, 'echo')

        result = invoke('close', 'f', '--snapshot', str(tmp_path / 'snapshot.json'))

        assert result.exit_code == 1
        assert result.stderr == 'Family `f` is scored by tests, so it has no votes.\n'

    def test_collects_real_test_trials_before_writing_their_aggregate(
        self,
        families_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_task_family(families_dir, 'echo')
        settings = families_dir / 'f/family.toml'
        settings.write_text(
            settings.read_text()
            + textwrap.dedent(
                """\
                systems = ["large"]
                harness = "terminus-2"
                """
            )
        )
        task = families_dir / 'f/tasks/echo'
        config = TaskConfig.model_validate_toml((task / 'task.toml').read_text())
        config.metadata['author'] = 'large'
        config.metadata['author_vendor'] = 'acme'
        (task / 'task.toml').write_text(config.model_dump_toml())
        family = load_family('f')
        system = make_system(name='large', display_name='Public Large')
        (resolved,) = jobs.family_systems(family, [system])
        finish_job(jobs.job(family, [resolved], [task]))
        monkeypatch.setattr(cli, '_load_systems', lambda: {'large': system})
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')

        result = invoke('close', 'f')
        assert result.exit_code == 0, result.output

        (path,) = (tmp_path / 'reports' / 'f').iterdir()
        closed = report.Report.model_validate_json(path.read_text())
        assert closed.results.scorer == 'tests'
        assert closed.results.standings[0].pass_rate.rate == 1.0
        assert closed.systems['large'].display_name == 'Public Large'
        assert closed.systems['large'].fingerprint == fingerprint(resolved)
        assert 'Family `f`: 1 outputs, 0 errored.' in result.output


class TestFingerprints:
    def test_names_an_undefined_family_system_without_a_traceback(
        self, families_dir: Path
    ) -> None:
        write_case_family(families_dir, ['missing'])

        result = invoke('fingerprints')

        assert result.exit_code == 1
        assert result.exception is not None
        assert isinstance(result.exception, SystemExit)
        assert 'Family `f` lists `missing`' in result.output
        assert '`systems.toml` does not define' in result.output

    @pytest.mark.parametrize(
        ('family', 'frozen', 'failure'),
        [
            pytest.param(None, None, None, id='no-reports'),
            pytest.param('writing-en', 'writing-en/opus-5.5-high', None, id='restored'),
            pytest.param('writing-en', None, None, id='unrecorded'),
            pytest.param(
                'writing-en', 'fp-old', 'writing-en', id='moved-after-a-close'
            ),
            pytest.param(
                'coding-repo',
                'writing-en/opus-5.5-high',
                'coding-repo',
                id='frozen-under-another-harness',
            ),
        ],
    )
    def test_fails_for_a_system_that_left_its_frozen_fingerprint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        family: str | None,
        frozen: str | None,
        failure: str | None,
    ) -> None:
        head = manifest.current_fingerprints(load_systems(load_export()))
        recorded = None if frozen is None else head.get(frozen, frozen)
        if family is None:
            monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        else:
            write_closed_report(tmp_path, monkeypatch, family, recorded)

        result = invoke('fingerprints')

        if failure is None:
            assert (result.exit_code, result.stdout, result.stderr) == (
                0,
                'Every system keeps the fingerprint its closed reports froze.\n',
                '',
            )
        else:
            current = head[f'{failure}/opus-5.5-high']
            assert (result.exit_code, result.stdout, result.stderr) == (
                1,
                '',
                f'Closed reports of `{failure}` froze `opus-5.5-high` under '
                f'fingerprint `{recorded}`, but it now resolves to `{current}`.\n'
                f'{RENAME_GUIDE}\n',
            )

    def test_names_conflicting_closed_reports_without_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_closed_report(tmp_path, monkeypatch, 'writing-en', 'fp-old')
        later = report.Report.model_validate_json(
            (tmp_path / 'reports' / 'writing-en' / '2026-10-21.json').read_text()
        )
        systems = later.systems | {
            'opus-5.5-high': later.systems['opus-5.5-high'].model_copy(
                update={'fingerprint': 'fp-new'}
            )
        }
        report.write_report(
            later.model_copy(update={'round': '2026-10-22', 'systems': systems})
        )

        result = invoke('fingerprints')

        assert (result.exit_code, result.output) == (
            1,
            'System `opus-5.5-high` reuses an identifier across fingerprints.\n',
        )
