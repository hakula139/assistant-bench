from pathlib import Path
from typing import Any

import pytest
from harbor.models.job.config import JobConfig
from typer.testing import CliRunner

from assistant_bench import jobs
from assistant_bench.cli import app


runner = CliRunner()


def output(**fields: Any) -> jobs.Output:
    empty = dict.fromkeys(jobs.Output.model_fields)
    return jobs.Output.model_validate(
        empty | {'trial': 't', 'job': 'j', 'case': 'c', 'system': 's'} | fields
    )


@pytest.fixture
def submitted(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> list[JobConfig]:
    """Records each job `bench` submits in place of running Harbor."""
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


class TestRun:
    def test_generates_the_listed_systems_on_every_case(
        self, submitted: list[JobConfig]
    ) -> None:
        result = runner.invoke(app, ['run', 'smoke-text'])

        assert result.exit_code == 0, result.output
        (job,) = submitted
        assert [agent.kwargs['system'] for agent in job.agents] == [
            'opus-5.5',
            'gpt-6.1-sol',
            'gemini-4-argon',
            'gemini-3.8-flash',
            'deepseek-v4.1-flash',
        ]
        assert [task.path.name for task in job.tasks if task.path] == [
            'en-greeting',
            'zh-greeting',
        ]

    def test_rejects_a_task_family(self, submitted: list[JobConfig]) -> None:
        result = runner.invoke(app, ['run', 'smoke'], terminal_width=200)

        assert result.exit_code == 2
        assert 'single-call systems run only case families' in result.output
        assert submitted == []


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
        assert result.output == (
            '3 outputs, 1 errored\n  TimeoutError: 1\nMean reward 0.500\n'
        )
