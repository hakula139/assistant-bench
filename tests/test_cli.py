import asyncio
import shutil
import subprocess
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig
from typer.testing import CliRunner

from assistant_bench import catalog, cli, jobs, report, revisions
from assistant_bench.arena import api
from assistant_bench.arena.store import VoteStore
from assistant_bench.catalog import load_export, load_systems
from assistant_bench.cli import app
from assistant_bench.families import Gate, load_cases, load_family
from assistant_bench.outputs import write_outputs
from assistant_bench.paths import FAMILIES_DIR
from support import (
    finish_job,
    make_output,
    write_synthetic_round,
    write_trial,
    write_votes_family,
)


runner = CliRunner()


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args), env={'COLUMNS': '200'})
    return result.exit_code, result.output


def write_cases_family(families_dir: Path, systems: str) -> None:
    directory = families_dir / 'f'
    directory.mkdir(parents=True)
    (directory / 'family.toml').write_text(
        textwrap.dedent(
            f"""\
            scorer = "votes"
            display_name = "Family F"
            systems = {systems}
            samples = 1
            max_output_tokens = 64
            reference = "opus-5.5"
            """
        )
    )
    (directory / 'cases.jsonl').write_text('')


@pytest.fixture
def submitted(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> list[JobConfig]:
    """Record each job `bench` submits in place of running Harbor."""
    configs: list[JobConfig] = []

    def run_job(job: JobConfig) -> Path:
        configs.append(job)
        return job.jobs_dir / job.job_name

    monkeypatch.setattr(jobs, 'run_job', run_job)
    return configs


class TestCheck:
    def test_counts_the_cases_of_a_case_family(
        self, submitted: list[JobConfig]
    ) -> None:
        result = runner.invoke(app, ['check', 'smoke-text'])

        assert result.exit_code == 0, result.output
        assert result.output == 'smoke-text: 2 valid cases\n'
        assert submitted == []

    @pytest.mark.parametrize(
        ('rewards', 'exit_code', 'message'),
        [
            ([1.0], 0, 'smoke: the oracle passed every task\n'),
            ([0.0], 1, 'smoke: the oracle failed on echo\n'),
            ([], 1, 'smoke: the oracle left no trial of echo\n'),
        ],
    )
    def test_runs_the_oracle_on_task_directories(
        self,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
        rewards: list[float],
        exit_code: int,
        message: str,
    ) -> None:
        monkeypatch.setattr(
            jobs,
            'read_trials',
            lambda family, job_dir, systems: [
                make_output(case='echo', reward=reward) for reward in rewards
            ],
        )

        result = runner.invoke(app, ['check', 'smoke'])

        assert (result.exit_code, result.output) == (exit_code, message)
        (job,) = submitted
        assert [agent.name for agent in job.agents] == ['oracle']

    def test_rejects_a_task_named_apart_from_its_directory(
        self,
        families_dir: Path,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        shutil.copytree(FAMILIES_DIR / 'smoke', families_dir / 'smoke')
        config = families_dir / 'smoke' / 'tasks' / 'echo' / 'task.toml'
        config.write_text(config.read_text().replace('smoke/echo', 'smoke/repeat'))
        monkeypatch.setattr(jobs, 'FAMILIES_DIR', families_dir)

        exit_code, message = invoke('check', 'smoke')

        assert exit_code == 2
        assert (
            'family \'smoke\': set [task] name to "smoke/echo" in tasks/echo/task.toml'
        ) in message
        assert submitted == []

    def test_rejects_a_missing_family(self) -> None:
        exit_code, message = invoke('check', 'nope')

        assert exit_code == 2
        assert "Invalid value for FAMILY: no family named 'nope'" in message

    @pytest.mark.parametrize(
        ('edited', 'expected'),
        [
            (
                ['en-greeting'],
                "family 'smoke-text': case 'en-greeting' changed, give it a new ID",
            ),
            (
                ['en-greeting', 'zh-greeting'],
                "family 'smoke-text': cases 'en-greeting', 'zh-greeting' changed, "
                'give each a new ID',
            ),
        ],
    )
    def test_rejects_cases_whose_task_differs_from_a_recorded_trial(
        self, data_dir: Path, edited: list[str], expected: str
    ) -> None:
        job_dir = data_dir / 'jobs' / 'smoke-text' / 'job-1'
        for case_id in edited:
            agent = AgentConfig(name='oracle')
            write_trial(job_dir, f'{case_id}__1', agent, task=f'smoke-text/{case_id}')

        exit_code, message = invoke('check', 'smoke-text')

        assert exit_code == 2
        assert expected in message

    def test_rejects_a_local_case_that_reuses_a_committed_id(
        self, data_dir: Path
    ) -> None:
        local = data_dir / 'cases' / 'smoke-text.jsonl'
        local.parent.mkdir(parents=True)
        local.write_text(
            '{"id": "en-greeting", "category": "reply", "source": "held-out", '
            '"instruction": "Hi."}\n'
        )

        exit_code, message = invoke('check', 'smoke-text')

        assert exit_code == 2
        assert "case 'en-greeting' appears at" in message

    def test_rejects_a_family_listing_an_unknown_system(
        self, families_dir: Path
    ) -> None:
        write_cases_family(families_dir, '["opus-5.5", "ghost"]')

        exit_code, message = invoke('check', 'f')

        assert exit_code == 2
        assert 'f lists unknown systems: ghost' in message

    def test_rejects_a_systems_table_that_fails_to_validate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / 'systems.toml'
        path.write_text('[broken]\neffort = "low"\n')
        monkeypatch.setattr(
            cli, 'load_systems', lambda export: catalog.load_systems(export, path)
        )

        exit_code, message = invoke('check', 'smoke-text')

        assert exit_code == 2
        assert 'Invalid value for systems.toml: 1 validation error' in message
        assert 'broken' in message


class TestRun:
    def test_generates_the_listed_systems_on_every_case(
        self, submitted: list[JobConfig]
    ) -> None:
        result = runner.invoke(app, ['run', 'smoke-text'])

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
        result = runner.invoke(
            app, ['run', 'smoke-text', '--system', 'kimi-k3', '--system', 'opus-5.5']
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
        write_cases_family(families_dir, '["opus-5.5"]')

        exit_code, message = invoke(
            'run', 'f', '--system', 'gpt-6-astra', '--system', 'opus-5.5'
        )

        assert exit_code == 2
        assert 'f does not list gpt-6-astra, so choose among opus-5.5' in message
        assert submitted == []

    def test_generates_after_a_named_run_only_the_systems_it_left_out(
        self, submitted: list[JobConfig]
    ) -> None:
        named = ['gpt-6.1-sol', 'kimi-k3']
        args = [arg for name in named for arg in ('--system', name)]
        result = runner.invoke(app, ['run', 'smoke-text', *args])
        assert result.exit_code == 0, result.output
        first = finish_job(submitted[0])

        result = runner.invoke(app, ['run', 'smoke-text'])

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

    def test_rejects_a_task_family(self, submitted: list[JobConfig]) -> None:
        exit_code, message = invoke('run', 'smoke')

        assert exit_code == 2
        assert 'single-call systems run only case families' in message
        assert submitted == []

    def test_exits_with_the_code_of_a_failed_harbor_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def run_job(job: JobConfig) -> Path:
            raise subprocess.CalledProcessError(3, 'harbor')

        monkeypatch.setattr(jobs, 'run_job', run_job)

        exit_code, _ = invoke('run', 'smoke-text')

        assert exit_code == 3


class TestIngest:
    def test_summarizes_gate_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            jobs,
            'ingest',
            lambda family, systems: [
                make_output(gate_failures=[]),
                make_output(gate_failures=[Gate.TRUNCATED, Gate.LENGTH]),
                make_output(gate_failures=[Gate.TRUNCATED]),
            ],
        )

        result = runner.invoke(app, ['ingest', 'smoke-text'])

        assert result.exit_code == 0, result.output
        assert result.output == textwrap.dedent(
            """\
            3 outputs, 0 errored
            2 failed a gate
              truncated: 2
              length: 1
            """
        )

    def test_summarizes_errors_and_rewards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            jobs,
            'ingest',
            lambda family, systems: [
                make_output(reward=1.0),
                make_output(reward=0.0),
                make_output(error='TimeoutError'),
            ],
        )

        result = runner.invoke(app, ['ingest', 'smoke'])

        assert result.exit_code == 0, result.output
        assert result.output == textwrap.dedent(
            """\
            3 outputs, 1 errored
              TimeoutError: 1
            Mean reward 0.500
            """
        )


class TestServe:
    def test_builds_the_local_site_and_serves_the_families_with_outputs(
        self, families_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_outputs('f', [make_output(case='c1', system='large', gate_failures=[])])
        site = tmp_path / 'dist'
        builds: list[tuple[list[str], str | None]] = []
        served: list[tuple[list[str], Path]] = []

        def build(args: list[str], **options: Any) -> None:
            builds.append((args, options['env'].get('BENCH_LOCAL')))
            (site / 'vote').mkdir(parents=True)
            (site / 'vote' / 'index.html').write_text('<h1>Vote</h1>')

        monkeypatch.setattr(cli, 'SITE_DIR', site)
        monkeypatch.setattr(subprocess, 'run', build)
        monkeypatch.setattr(
            api,
            'serve',
            lambda arena, site: served.append(
                ([family.name for family in arena.families()], site)
            ),
        )

        exit_code, message = invoke('serve')

        assert exit_code == 0, message
        assert builds == [(['pnpm', '--filter', 'web', 'build'], '1')]
        assert served == [(['f'], site)]
        assert message == 'Voting on f at http://127.0.0.1:8790/vote/\n'

    def test_stops_when_the_build_leaves_no_vote_page(
        self, families_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_outputs('f', [make_output(case='c1', system='large', gate_failures=[])])
        monkeypatch.setattr(cli, 'SITE_DIR', tmp_path / 'dist')
        monkeypatch.setattr(subprocess, 'run', lambda args, **options: None)
        monkeypatch.setattr(api, 'serve', lambda arena, site: pytest.fail('served'))

        exit_code, message = invoke('serve')

        assert exit_code == 1
        assert f'No vote page at {tmp_path / "dist" / "vote" / "index.html"}' in message
        assert '`BENCH_LOCAL=1 pnpm --filter web build`' in message

    def test_exits_with_the_code_of_a_failed_site_build(
        self, families_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})
        write_outputs('f', [make_output(case='c1', system='large', gate_failures=[])])

        def build(args: list[str], **options: Any) -> None:
            raise subprocess.CalledProcessError(4, args)

        monkeypatch.setattr(subprocess, 'run', build)

        exit_code, _ = invoke('serve')

        assert exit_code == 4

    def test_stops_when_no_family_has_outputs(self, families_dir: Path) -> None:
        exit_code, message = invoke('serve')

        assert exit_code == 1
        assert 'No family scored by votes has outputs yet' in message


class TestClose:
    def test_writes_the_family_report(
        self,
        families_dir: Path,
        store: VoteStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_synthetic_round(families_dir, store, sessions=1)
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')

        exit_code, message = invoke('close', 'arena')

        assert exit_code == 0, message
        (path,) = (tmp_path / 'reports' / 'arena').iterdir()
        assert message == f'Report written to {path}\n'
        assert report.Report.model_validate_json(path.read_text()).family == 'arena'

    def test_rejects_a_family_without_votes(self, families_dir: Path) -> None:
        write_votes_family(families_dir, 'f', ['large', 'small'], {'c1': 'reply'})

        exit_code, message = invoke('close', 'f')

        assert exit_code == 2
        assert "family 'f' has no votes" in message


class TestFingerprints:
    @pytest.mark.parametrize(
        ('changed', 'exit_code', 'message'),
        [
            (False, 0, 'Every system keeps its fingerprint since origin/main'),
            (
                True,
                1,
                "system 'opus-5.5' changed its fingerprint since origin/main, "
                'so give the changed system a new name',
            ),
        ],
    )
    def test_compares_each_system_with_the_base_revision(
        self,
        monkeypatch: pytest.MonkeyPatch,
        changed: bool,
        exit_code: int,
        message: str,
    ) -> None:
        head = load_systems(load_export())
        opus = head['opus-5.5']
        old = opus.model_copy(update={'route': f'{opus.route}-old'})
        base = head | {'opus-5.5': old} if changed else head
        monkeypatch.setattr(revisions, 'systems_at', lambda revision: base)

        result = runner.invoke(app, ['fingerprints', 'origin/main'])

        assert (result.exit_code, result.output) == (exit_code, f'{message}\n')
