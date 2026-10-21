import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import AgentConfig
from typer.testing import CliRunner

from assistant_bench import jobs, revisions
from assistant_bench.catalog import load_export, load_systems
from assistant_bench.cli import app
from assistant_bench.families import load_cases, load_family
from support import write_trial


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
            systems = {systems}
            samples = 1
            max_output_tokens = 64
            """
        )
    )
    (directory / 'cases.jsonl').write_text('')


def output(**fields: Any) -> jobs.Output:
    empty = dict.fromkeys(jobs.Output.model_fields)
    return jobs.Output.model_validate(
        empty | {'trial': 't', 'job': 'j', 'case': 'c', 'system': 's'} | fields
    )


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
        ('reward', 'exit_code', 'message'),
        [
            (1.0, 0, 'smoke: the oracle passed every task\n'),
            (0.0, 1, 'smoke: the oracle failed on echo\n'),
        ],
    )
    def test_runs_the_oracle_on_task_directories(
        self,
        submitted: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
        reward: float,
        exit_code: int,
        message: str,
    ) -> None:
        monkeypatch.setattr(
            jobs,
            'read_trials',
            lambda family, job_dir, systems: [output(case='echo', reward=reward)],
        )

        result = runner.invoke(app, ['check', 'smoke'])

        assert (result.exit_code, result.output) == (exit_code, message)
        (job,) = submitted
        assert [agent.name for agent in job.agents] == ['oracle']

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

    def test_rejects_a_task_family(self, submitted: list[JobConfig]) -> None:
        exit_code, message = invoke('run', 'smoke')

        assert exit_code == 2
        assert 'single-call systems run only case families' in message
        assert submitted == []

    def test_rejects_a_family_listing_no_systems(
        self, families_dir: Path, submitted: list[JobConfig]
    ) -> None:
        write_cases_family(families_dir, '[]')

        exit_code, message = invoke('run', 'f')

        assert exit_code == 2
        assert 'f lists no systems' in message
        assert submitted == []

    def test_exits_with_the_code_of_a_failed_harbor_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def run_job(job: JobConfig) -> Path:
            raise subprocess.CalledProcessError(3, 'harbor')

        monkeypatch.setattr(jobs, 'run_job', run_job)

        exit_code, _ = invoke('run', 'smoke-text')

        assert exit_code == 3


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


class TestIngest:
    def test_summarizes_errors_and_rewards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            jobs,
            'ingest',
            lambda family, systems: [
                output(reward=1.0),
                output(reward=0.0),
                output(error='TimeoutError'),
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
