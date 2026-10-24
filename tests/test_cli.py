import asyncio
import os
import sqlite3
import subprocess
import textwrap
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.task.config import TaskConfig
from harbor.models.trial.config import AgentConfig
from rich.text import Text
from typer.testing import CliRunner, Result

from assistant_bench import catalog, cli, jobs, report, revisions
from assistant_bench.arena import api
from assistant_bench.arena.service import Arena
from assistant_bench.arena.store import Ballot, Side, VoteStore, votes_file
from assistant_bench.catalog import EXPORT_VAR, load_export, load_systems
from assistant_bench.cli import app
from assistant_bench.families import Gate, family_names, load_cases, load_family
from assistant_bench.outputs import Output, write_outputs
from support import (
    finish_job,
    make_output,
    make_system,
    write_case_family,
    write_task_family,
    write_trial,
    write_votes_family,
)


runner = CliRunner()


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


def write_job(job_dir: Path) -> None:
    """Write the files that mark a finished Harbor job without trials."""
    job_dir.mkdir(parents=True)
    (job_dir / 'config.json').touch()
    (job_dir / 'result.json').touch()


def write_removed_case(families_dir: Path, data_dir: Path) -> None:
    """Write a script-gated votes family whose trial ran a case that is then removed."""
    write_votes_family(
        families_dir, 'f', ['large', 'small'], {'a': 'reply', 'b': 'reply'}
    )
    config = families_dir / 'f' / 'family.toml'
    config.write_text(config.read_text() + '\n[gates]\nscript = "hans"\n')
    family = load_family('f')
    cases = load_cases(family)
    for task in jobs.render_tasks(family, cases):
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            f'{task.name}__large__1',
            AgentConfig(
                import_path='x:Y', kwargs={'system': 'large', 'fingerprint': 'fp'}
            ),
            task=f'f/{task.name}',
            output='你好，世界。',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
        )
    jobs.render_tasks(family, cases[:1])


def write_version_one_store() -> None:
    path = votes_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA user_version = 1')


@pytest.fixture
def submitted(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> list[JobConfig]:
    """Record each job `bench` submits in place of running Harbor."""
    configs: list[JobConfig] = []

    def run_job(job: JobConfig) -> Path:
        configs.append(job)
        return jobs.job_path(job)

    monkeypatch.setattr(jobs, 'run_job', run_job)
    return configs


@dataclass
class Site:
    """The stub site build and what `bench serve` served from it."""

    path: Path
    builds: list[tuple[list[str], str | None]] = field(default_factory=list)
    served: list[tuple[list[str], Path]] = field(default_factory=list)


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Site:
    """Replace the site build with one that writes a vote page, and record serves."""
    stub = Site(tmp_path / 'dist')

    def build(args: list[str], **options: Any) -> None:
        stub.builds.append((args, os.environ.get(cli.LOCAL_SITE_VAR)))
        (stub.path / 'vote').mkdir(parents=True)
        (stub.path / 'vote' / 'index.html').write_text('<h1>Vote</h1>')

    def serve(arena: Arena, site: Path) -> None:
        stub.served.append(([family.name for family in arena.families()], site))

    monkeypatch.setattr(cli, 'SITE_DIR', stub.path)
    monkeypatch.setattr(subprocess, 'run', build)
    monkeypatch.setattr(api, 'serve', serve)
    return stub


class TestMain:
    def test_sets_the_environment_harbor_runs_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in jobs.HARBOR_ENV:
            monkeypatch.delenv(name)

        result = invoke('check', 'smoke-text')
        assert result.exit_code == 0, result.output

        assert {name: os.environ.get(name) for name in jobs.HARBOR_ENV} == {
            'HARBOR_TELEMETRY': 'off',
            'LITELLM_MODE': 'PRODUCTION',
        }


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
        write_case_family(families_dir, ['opus-5.5'], name='f')
        write_case_family(families_dir, ['opus-5.5'], name='g')

        result = invoke('check')
        assert result.exit_code == 0, result.output

        assert result.output == (
            'Family `f`: 0 valid cases.\nFamily `g`: 0 valid cases.\n'
        )
        assert submitted == []

    def test_checks_a_named_family_once(self, families_dir: Path) -> None:
        write_case_family(families_dir, ['opus-5.5'])

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
        monkeypatch.setattr(
            jobs, 'read_trials', lambda family, job_dir, systems: outputs
        )

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
        write_case_family(families_dir, ['opus-5.5'], name='cases')

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
        monkeypatch.setattr(
            jobs, 'read_trials', lambda family, job_dir, systems: outputs
        )
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
        assert (
            '`tasks/echo/task.toml`: set `[metadata] author` to a system name.'
            in result.output
        )
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
        monkeypatch.setattr(
            jobs, 'read_trials', lambda family, job_dir, systems: outputs[family.name]
        )

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
            AgentConfig(import_path='x:Y', kwargs={'system': 'opus-5.5'}),
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
        config = write_case_family(families_dir, ['opus-5.5']).with_name('family.toml')
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
        local = data_dir / 'cases' / 'smoke-text.jsonl'
        local.parent.mkdir(parents=True)
        local.write_text(
            '{"id": "en-greeting", "category": "message", "source": "held-out", '
            '"instruction": "Hi."}\n'
        )

        result = invoke('check', 'smoke-text')

        assert result.exit_code == 1
        assert 'case `en-greeting` appears at' in result.output

    def test_rejects_a_family_whose_harness_cannot_run_a_listed_system(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_task_family(families_dir, 'echo', systems=('opus-5.5',))
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
        write_task_family(families_dir, 'echo', systems=('opus-5.5',))
        systems = load_systems(load_export())
        prompted = systems['opus-5.5'].model_copy(update={'prompt': 'Be brief.'})
        monkeypatch.setattr(
            cli, 'load_systems', lambda export: systems | {'opus-5.5': prompted}
        )

        result = invoke('check', 'f')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `f` lists `opus-5.5`, whose prompt the `terminus-2` harness '
            'cannot send. Remove them from `systems`.\n',
        )
        assert submitted == []

    def test_rejects_a_family_listing_an_unknown_system(
        self, families_dir: Path
    ) -> None:
        write_case_family(families_dir, ['opus-5.5', 'ghost'])

        result = invoke('check', 'f')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `f` lists `ghost`, which `systems.toml` does not define. Add a '
            'table to `systems.toml` or fix the name in `families/f/family.toml`.\n',
        )

    def test_rejects_a_systems_table_that_fails_to_validate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / 'systems.toml'
        path.write_text('[broken]\neffort = "low"\n')
        monkeypatch.setattr(
            cli, 'load_systems', lambda export: catalog.load_systems(export, path)
        )

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
        write_case_family(families_dir, ['opus-5.5', 'opus-5.5'])

        result = invoke('run', 'f')

        assert (result.exit_code, result.stdout, result.stderr) == (
            1,
            '',
            'Family `f`: `family.toml`: `systems` repeats `opus-5.5`.\n',
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
            'run', 'smoke-text', '--system', 'kimi-k3', '--system', 'opus-5.5'
        )
        assert result.exit_code == 0, result.output

        (job,) = submitted
        assert [agent.kwargs['system'] for agent in job.agents] == [
            'opus-5.5',
            'kimi-k3',
        ]

    def test_rejects_a_named_system_the_family_does_not_list(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5'])

        result = invoke('run', 'f', '--system', 'gpt-6-astra', '--system', 'opus-5.5')

        assert result.exit_code == 2
        assert (
            'Family `f` does not list `gpt-6-astra`. Choose among `opus-5.5`.'
            in result.output
        )
        assert submitted == []

    def test_generates_after_a_named_run_only_the_systems_it_left_out(
        self, submitted: list[JobConfig]
    ) -> None:
        named = ['gpt-6.1-sol', 'kimi-k3']
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
        write_task_family(families_dir, 'echo', systems=('opus-5.5',))

        result = invoke('run', 'f')
        assert result.exit_code == 0, result.output

        (job,) = submitted
        (agent,) = job.agents
        opus = load_systems(load_export())['opus-5.5']
        assert agent.import_path == 'assistant_bench.harnesses.terminus:Terminus'
        assert agent.model_name == f'openai/{opus.route}'
        assert agent.kwargs['fingerprint'] == (
            opus.model_copy(update={'harness': 'terminus-2'}).fingerprint
        )
        assert agent.kwargs['max_turns'] == 4
        assert agent.kwargs['llm_call_kwargs'] == {'max_completion_tokens': 64}
        assert job.n_concurrent_trials == jobs.N_CONCURRENT_CONTAINERS
        assert not job.verifier.disable
        assert [task.path for task in job.tasks] == [
            families_dir / 'f' / 'tasks' / 'echo'
        ]

    def test_runs_after_a_task_family_run_only_the_systems_it_left_out(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_task_family(families_dir, 'echo', systems=('opus-5.5', 'kimi-k3'))
        result = invoke('run', 'f', '--system', 'opus-5.5')
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
        } == {'opus-5.5': DiffAction.REUSE, 'kimi-k3': DiffAction.RERUN}

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
        write_case_family(families_dir, ['opus-5.5'], name='f')
        write_case_family(families_dir, ['opus-5.5', 'kimi-k3'], name='g')
        write_task_family(families_dir, name='z')

        result = invoke('run')
        assert result.exit_code == 0, result.output

        assert [job.jobs_dir.name for job in submitted] == ['f', 'g']
        assert [len(job.agents) for job in submitted] == [1, 2]

    def test_generates_a_named_family_once(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5'])

        result = invoke('run', 'f', 'f')
        assert result.exit_code == 0, result.output

        (job,) = submitted
        assert job.jobs_dir.name == 'f'

    def test_generates_a_named_system_on_every_family_that_lists_it(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5'], name='f')
        write_case_family(families_dir, ['opus-5.5', 'kimi-k3'], name='g')

        result = invoke('run', '--system', 'kimi-k3')
        assert result.exit_code == 0, result.output

        (job,) = submitted
        assert job.jobs_dir.name == 'g'
        assert [agent.kwargs['system'] for agent in job.agents] == ['kimi-k3']

    def test_rejects_a_named_system_no_family_lists(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5'], name='f')

        result = invoke('run', '--system', 'gpt-6-astra')

        assert result.exit_code == 2
        assert (
            'No family lists `gpt-6-astra` in its `systems`. Check the name '
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
                AgentConfig(name='generate'),
                task='smoke-text/en-greeting',
                output='Hi.',
                context=AgentContext(metadata={'finish_reason': 'stop'}),
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
        write_case_family(families_dir, ['opus-5.5'], name='f')
        write_case_family(families_dir, ['opus-5.5'], name='g')
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
                AgentConfig(name='generate'),
                task='smoke-text/en-greeting',
                output='Hi.',
                context=AgentContext(metadata={'finish_reason': 'stop'}),
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

    def test_rejects_a_system_whose_recorded_fingerprint_changed(
        self, families_dir: Path, data_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5'], name='f')
        write_trial(
            data_dir / 'jobs' / 'f' / '2026-10-20__10-00-00',
            'c1__1',
            AgentConfig(
                import_path='x:Y',
                kwargs={'system': 'opus-5.5', 'fingerprint': 'fp-old'},
            ),
            task='f/c1',
        )
        current = load_systems(load_export())['opus-5.5'].fingerprint

        result = invoke('run', 'f')

        assert (result.exit_code, result.output) == (
            1,
            'Family `f` has systems that changed their fingerprints:\n'
            'System `opus-5.5` ran under fingerprint `fp-old` but now resolves '
            f'to `{current}`.\nGive a changed system a new name, or keep its old '
            'resolution on a `route` table, as `docs/guide/authoring.md#systems` '
            'describes.\n',
        )
        assert submitted == []

    def test_refuses_a_later_family_before_running_any(
        self, families_dir: Path, data_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5'], name='f')
        cases = write_case_family(families_dir, ['opus-5.5'], name='g')
        cases.write_text(
            '{"id": "c1", "category": "message", "source": "synthetic", '
            '"instruction": "Hi."}\n'
        )
        write_trial(
            data_dir / 'jobs' / 'g' / '2026-10-20__10-00-00',
            'old__1',
            AgentConfig(
                import_path='x:Y',
                kwargs={'system': 'opus-5.5', 'fingerprint': 'fp-old'},
            ),
            task='g/old',
        )

        result = invoke('run')

        assert result.exit_code == 1
        assert 'System `opus-5.5` ran under fingerprint `fp-old`' in result.output
        assert submitted == []
        assert not (data_dir / 'tasks' / 'g' / 'c1').exists()

    def test_refuses_a_task_edited_without_a_bump_before_running_any(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_case_family(families_dir, ['opus-5.5'], name='f')
        write_task_family(families_dir, 'echo', name='z', systems=('opus-5.5',))
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
            "Bump each task's `[task] version`: the patch for a change no trial "
            'depends on, such as its solution, the minor for a change to `tests/` '
            'alone, which regrades its trials, or the major for anything the '
            'agent sees, which reruns them.\n',
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
        write_case_family(families_dir, ['opus-5.5'], name='f')
        job = data_dir / 'jobs' / 'f' / '2026-10-20__10-00-00'
        write_trial(
            job,
            'c1__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 'opus-5.5'}),
            task='f/c1',
        )
        (job / 'c1__1' / 'lock.json').unlink()

        result = invoke('run', 'f')

        assert result.exit_code == 1
        assert 'has a result.json but no lock.json' in result.output
        assert submitted == []


class TestCollect:
    def test_reports_an_unknown_task_author_without_a_traceback(
        self, families_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_task_family(families_dir, 'echo', systems=('large',))
        path = families_dir / 'f' / 'tasks' / 'echo' / 'task.toml'
        path.write_text(
            path.read_text().replace('author = "large"', 'author = "unknown"')
        )
        monkeypatch.setattr(
            cli, '_load_systems', lambda: {'large': make_system(name='large')}
        )
        monkeypatch.setattr(jobs, 'collect', lambda family, systems: [])

        result = invoke('collect', 'f')

        assert result.exit_code == 1
        assert (
            '`tasks/echo/task.toml`: author `unknown` is not defined in `systems.toml`.'
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
                path.read_text().replace('author = "large"', 'author = "writer"')
            )
        outputs = [
            make_output(
                case=case,
                system=system,
                reward=float(case.startswith('own') == (system == 'large')),
            )
            for case in ('own-a', 'own-b', 'other-a', 'other-b')
            for system in ('large', 'small')
        ]
        monkeypatch.setattr(cli, '_load_systems', lambda: systems)
        monkeypatch.setattr(jobs, 'collect', lambda family, systems: outputs)

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
        monkeypatch.setattr(
            jobs,
            'collect',
            lambda family, systems: [
                make_output(case='one', system='large', reward=reward)
            ],
        )

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
        monkeypatch.setattr(jobs, 'collect', lambda family, systems: outputs)

        result = invoke('collect', name)
        assert result.exit_code == 0, result.output

        assert result.output == textwrap.dedent(summary)

    def test_collects_every_family_with_jobs(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        for name in ('f', 'g', 'h'):
            write_case_family(families_dir, ['opus-5.5'], name=name)
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
        write_case_family(families_dir, ['opus-5.5'])
        trial = data_dir / 'jobs' / 'f' / 'job-1' / 'c1__1'
        write_trial(
            trial.parent,
            trial.name,
            AgentConfig(import_path='x:Y', kwargs={'system': 'opus-5.5'}),
            task='f/c1',
        )
        (trial / 'result.json').write_text('{')

        result = invoke('collect', 'f')

        assert result.exit_code == 1
        assert result.output.startswith(
            f"Source trial '{trial.resolve()}' has an unreadable result.json"
        )


class TestServe:
    def test_builds_the_local_site_and_serves_the_families_with_outputs(
        self, families_dir: Path, site: Site
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_outputs('f', [make_output(case='c1', system='large', gate_failures=[])])

        result = invoke('serve')
        assert result.exit_code == 0, result.output

        assert site.builds == [(['pnpm', '--filter', 'web', 'build'], '1')]
        assert site.served == [(['f'], site.path)]
        assert result.output == (
            'Voting on `f` at http://127.0.0.1:8790/vote/. Open that exact address, '
            'since the server refuses `localhost`.\n'
        )

    def test_serves_after_a_case_is_removed(
        self, families_dir: Path, data_dir: Path, site: Site
    ) -> None:
        write_removed_case(families_dir, data_dir)

        result = invoke('serve')
        assert result.exit_code == 0, result.output

        assert [names for names, _ in site.served] == [['f']]

    def test_stops_when_the_build_leaves_no_vote_page(
        self, families_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_outputs('f', [make_output(case='c1', system='large', gate_failures=[])])
        monkeypatch.setattr(cli, 'SITE_DIR', tmp_path / 'dist')
        monkeypatch.setattr(subprocess, 'run', lambda args, **options: None)
        monkeypatch.setattr(api, 'serve', lambda arena, site: pytest.fail('served'))

        result = invoke('serve')

        assert result.exit_code == 1
        assert (
            f'No vote page at `{tmp_path / "dist" / "vote" / "index.html"}`.'
            in result.output
        )
        assert '`BENCH_LOCAL=1 pnpm --filter web build`' in result.output

    def test_points_at_pnpm_install_when_the_site_build_fails(
        self, families_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_outputs('f', [make_output(case='c1', system='large', gate_failures=[])])

        def build(args: list[str], **options: Any) -> None:
            raise subprocess.CalledProcessError(4, args)

        monkeypatch.setattr(subprocess, 'run', build)

        result = invoke('serve')

        assert (result.exit_code, result.output) == (
            4,
            'Building the vote page failed. If the checkout lacks node_modules, run '
            '`pnpm install` once, then rerun `uv run bench serve`.\n',
        )

    def test_collects_every_votes_family_with_jobs_before_loading(
        self, families_dir: Path, data_dir: Path, site: Site
    ) -> None:
        for name in ('f', 'g', 'h'):
            write_votes_family(families_dir, name, ['large', 'small'], {'c1': 'reply'})
        write_task_family(families_dir, name='z')
        write_job(data_dir / 'jobs' / 'z' / 'job-1')
        for name in ('f', 'g'):
            write_trial(
                data_dir / 'jobs' / name / 'job-1',
                'c1__large__1',
                AgentConfig(
                    import_path='x:Y',
                    kwargs={'system': 'large', 'fingerprint': 'fp-large'},
                ),
                task=f'{name}/c1',
                output='text',
                context=AgentContext(metadata={'finish_reason': 'stop'}),
            )

        result = invoke('serve')
        assert result.exit_code == 0, result.output

        assert (data_dir / 'outputs' / 'f.jsonl').exists()
        assert (data_dir / 'outputs' / 'g.jsonl').exists()
        assert not (data_dir / 'outputs' / 'h.jsonl').exists()
        assert not (data_dir / 'outputs' / 'z.jsonl').exists()
        assert [names for names, _ in site.served] == [['f', 'g']]

    def test_stops_when_no_family_has_outputs(self, families_dir: Path) -> None:
        result = invoke('serve')

        assert (result.exit_code, result.output) == (
            1,
            'No family scored by votes has outputs yet. Run '
            '`uv run bench run <family>` first.\n',
        )

    def test_reports_a_store_of_another_schema_version(
        self, families_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_outputs('f', [make_output(case='c1', system='large', gate_failures=[])])
        write_version_one_store()
        monkeypatch.setattr(
            subprocess, 'run', lambda *args, **options: pytest.fail('built the site')
        )

        result = invoke('serve')

        assert result.exit_code == 1
        assert 'has schema version 1, while this bench reads version 2' in result.output


class TestClose:
    def test_collects_the_family_before_writing_the_report(
        self,
        families_dir: Path,
        store: VoteStore,
        data_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_votes_family(
            families_dir, 'f', ['large', 'small'], {'c1': 'reply', 'c2': 'reply'}
        )
        job = data_dir / 'jobs' / 'f' / '2026-10-20__10-00-00'
        for case in ('c1', 'c2'):
            for system in ('large', 'small'):
                write_trial(
                    job,
                    f'{case}__{system}__1',
                    AgentConfig(
                        import_path='x:Y',
                        kwargs={'system': system, 'fingerprint': f'fp-{system}'},
                    ),
                    task=f'f/{case}',
                    output=f'{system} answers {case}',
                    context=AgentContext(metadata={'finish_reason': 'stop'}),
                    instruction=None,
                )
        outputs = jobs.collect(load_family('f'), {})
        by_key = {(output.case, output.system): output for output in outputs}
        at = datetime(2026, 10, 22, tzinfo=UTC)
        session = store.start_session('f', at)
        for case in ('c1', 'c2'):
            store.record(
                Ballot(
                    session=session.id,
                    case=case,
                    a=Side.of(by_key[case, 'large']),
                    b=Side.of(by_key[case, 'small']),
                    choice='a',
                    duration_sec=3.0,
                ),
                at,
            )
        (data_dir / 'outputs' / 'f.jsonl').unlink()
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')

        result = invoke('close', 'f')
        assert result.exit_code == 0, result.output

        (path,) = (tmp_path / 'reports' / 'f').iterdir()
        assert result.output == (
            f'Family `f`: 4 outputs, 0 errored.\nReport written to `{path}`.\n'
        )
        closed = report.Report.model_validate_json(path.read_text())
        assert isinstance(closed.results, report.VotesResults)
        assert closed.family == 'f'
        assert {standing.system for standing in closed.results.standings} == {
            'large',
            'small',
        }

    def test_rejects_a_family_without_outputs(self, families_dir: Path) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})

        result = invoke('close', 'f')

        assert result.exit_code == 1
        assert 'Family `f` has no outputs on its current cases.' in result.output
        assert 'Usage:' not in result.output

    def test_collects_past_a_removed_case_before_closing(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_removed_case(families_dir, data_dir)

        result = invoke('close', 'f')

        assert result.exit_code == 1
        assert 'Family `f` has no votes yet.' in result.output

    def test_reports_a_store_of_another_schema_version(
        self, families_dir: Path
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_version_one_store()

        result = invoke('close', 'f')

        assert result.exit_code == 1
        assert 'has schema version 1, while this bench reads version 2' in result.output
        assert 'Invalid value for' not in result.output


class TestFingerprints:
    def test_passes_when_every_system_keeps_its_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = load_systems(load_export())
        monkeypatch.setattr(revisions, 'systems_at', lambda revision: base)

        result = invoke('fingerprints', 'origin/main')

        assert (result.exit_code, result.output) == (
            0,
            'Every system keeps its fingerprint since `origin/main`.\n',
        )

    @pytest.mark.parametrize(
        'fields',
        [
            {},
            {'display_name': 'Claude "Opus" 5.5\\\x07 ·'},
            {'effort': None, 'price': None},
        ],
        ids=['route-only', 'escaped-display-name', 'unset-effort-and-price'],
    )
    def test_prints_the_base_resolution_for_a_changed_system(
        self, monkeypatch: pytest.MonkeyPatch, fields: dict[str, Any]
    ) -> None:
        head = load_systems(load_export())
        old = head['opus-5.5'].model_copy(update={'route': 'moved'} | fields)
        monkeypatch.setattr(
            revisions, 'systems_at', lambda revision: head | {'opus-5.5': old}
        )

        result = invoke('fingerprints', 'origin/main')

        assert result.exit_code == 1
        assert result.stderr == (
            'System `opus-5.5` changed its fingerprint since `origin/main`. Give '
            'the changed system a new name, and keep the old name on its old '
            'resolution with the table below, as `docs/guide/authoring.md#systems` '
            'describes.\n'
        )
        assert catalog.parse_systems(load_export(), result.stdout) == {'opus-5.5': old}

    def test_prints_no_table_for_a_prompt_system(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        head = load_systems(load_export())
        opus = head['opus-5.5']
        old = opus.model_copy(update={'route': 'moved', 'prompt': 'Be brief.'})
        monkeypatch.setattr(
            revisions, 'systems_at', lambda revision: head | {'opus-5.5': old}
        )

        result = invoke('fingerprints', 'origin/main')

        assert result.exit_code == 1
        assert result.stdout == ''
        assert result.stderr == (
            'System `opus-5.5` changed its fingerprint since `origin/main`, and a '
            '`route` table cannot pin its prompt. Give the changed system a new '
            'name and keep the old name only in closed reports, as '
            '`docs/guide/authoring.md#systems` describes.\n'
        )

    def test_exits_with_the_code_of_a_failed_revision_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def systems_at(revision: str) -> dict[str, Any]:
            raise subprocess.CalledProcessError(128, 'git')

        monkeypatch.setattr(revisions, 'systems_at', systems_at)

        result = invoke('fingerprints', 'nope')

        assert result.exit_code == 128
