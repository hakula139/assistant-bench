import asyncio
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.environment_type import EnvironmentType
from harbor.models.job import config as job_config
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.task.task import Task
from harbor.models.trial import config as trial_config
from harbor.trial.queue import TrialQueue
from openai.types.responses import Response

from assistant_bench import jobs
from assistant_bench.catalog import System
from assistant_bench.families import (
    Family,
    case_task,
    load_cases,
    load_family,
)
from assistant_bench.harnesses import HOST_ENVIRONMENT, fingerprint
from assistant_bench.harnesses.native import NativeCompletionError
from assistant_bench.harnesses.responses import ResponseResult
from assistant_bench.tasks import render_tasks
from support import (
    FIXTURES,
    START,
    VOTES,
    break_setting,
    finish_job,
    make_system,
    response,
    voted_tasks,
    with_settings,
    write_task_family,
    write_trial,
    write_votes_family,
)


class RetryProbe(TrialQueue):
    def retries(self, name: str) -> bool:
        return self._should_retry_exception(name)


class TestCheckSystems:
    @pytest.mark.parametrize(
        ('harness', 'system', 'message'),
        [
            pytest.param(
                'unknown',
                make_system(),
                'Family `f`: harness `unknown` is not one of `generate`, '
                '`terminus-2`, `claude-code`, `codex`.',
                id='unknown-harness',
            ),
            pytest.param(
                'generate',
                make_system(),
                'Family `f`: harness `generate` makes one request and never acts '
                "in a task's environment, so choose among `terminus-2`, "
                '`claude-code`, `codex`.',
                id='single-call-harness',
            ),
            pytest.param(
                'terminus-2',
                make_system(prompt='Be brief.'),
                'Family `f` lists `acme-large`, whose prompt the `terminus-2` '
                'harness cannot send. Remove them from `systems`.',
                id='unsent-prompt',
            ),
            pytest.param(
                'terminus-2',
                make_system(harness='codex', version='1.2.3'),
                'System `acme-large`: harness `codex` accepts '
                '`openai-responses` (got `openai-chat-completions`).',
                id='installed-wrong-api',
            ),
            pytest.param(
                'codex',
                make_system(api='openai-responses'),
                'System `acme-large`: set its `harness` and pinned `version` '
                'in `systems.toml`.',
                id='installed-missing-version',
            ),
        ],
    )
    def test_rejects_a_system_the_harness_cannot_run(
        self, families_dir: Path, harness: str, system: System, message: str
    ) -> None:
        family = with_settings(voted_tasks(families_dir), harness=harness)

        with pytest.raises(ValueError) as error:
            jobs.check_systems(family, [system])

        assert str(error.value) == message

    @pytest.mark.parametrize(
        ('family', 'system'),
        [
            pytest.param(VOTES, make_system(prompt='Be brief.'), id='case-family'),
            pytest.param(None, make_system(), id='task-family'),
        ],
    )
    def test_accepts_a_system_the_harness_can_run(
        self, families_dir: Path, family: Family | None, system: System
    ) -> None:
        family = family or voted_tasks(families_dir)

        jobs.check_systems(family, [system])


class TestFamilySystems:
    def test_runs_every_system_of_a_task_family_under_its_harness(
        self, families_dir: Path
    ) -> None:
        family = voted_tasks(families_dir)
        system = make_system()

        (run,) = jobs.family_systems(family, [system])

        assert run == system.model_copy(update={'harness': 'terminus-2'})
        assert fingerprint(run) != fingerprint(system)

    def test_keeps_the_systems_of_a_case_family(self) -> None:
        systems = [make_system(prompt='Be brief.')]

        run = jobs.family_systems(VOTES, systems)

        assert run == systems


class TestRunnableSystems:
    def test_names_an_undefined_system_and_its_family(self, families_dir: Path) -> None:
        write_votes_family(families_dir, ['missing'], name='writing')

        with pytest.raises(
            ValueError, match='Family `writing`.*`missing`.*systems.toml'
        ):
            jobs.runnable_systems({})

    def test_resolves_only_listed_systems_under_each_family_harness(
        self, families_dir: Path
    ) -> None:
        write_votes_family(families_dir, ['large'], name='writing')
        write_task_family(families_dir, 'echo', name='coding', systems=('large',))
        write_task_family(families_dir, name='smoke')
        system = make_system(name='large')

        runnable = jobs.runnable_systems(
            {'large': system, 'author-only': make_system(name='author-only')}
        )

        assert runnable == {
            'coding': [system.model_copy(update={'harness': 'terminus-2'})],
            'smoke': [],
            'writing': [system],
        }


class TestDriftedSystems:
    CURRENT = make_system()

    @pytest.mark.parametrize(
        ('kwargs', 'expected'),
        [
            pytest.param(
                {'system': CURRENT.name, 'fingerprint': 'fp-old'},
                [(CURRENT.name, 'fp-old', fingerprint(CURRENT))],
                id='changed-fingerprint',
            ),
            pytest.param(
                {'system': CURRENT.name, 'fingerprint': fingerprint(CURRENT)},
                [],
                id='kept-fingerprint',
            ),
            pytest.param(
                {'system': 'retired', 'fingerprint': 'fp-old'},
                [],
                id='unselected-system',
            ),
            pytest.param({'system': CURRENT.name}, [], id='no-recorded-fingerprint'),
        ],
    )
    def test_returns_each_selected_system_whose_trial_recorded_another_fingerprint(
        self,
        data_dir: Path,
        kwargs: dict[str, str],
        expected: list[tuple[str, str, str]],
    ) -> None:
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            trial_config.AgentConfig(import_path='x:Y', kwargs=kwargs),
            task='f/a',
        )

        drifted = jobs.drifted_systems(VOTES, [self.CURRENT])

        assert drifted == expected

    def test_returns_each_system_once_per_recorded_fingerprint(
        self, data_dir: Path
    ) -> None:
        large = make_system()
        small = make_system(name='acme-small', route='acme/small')
        for trial, system in (
            ('a__small', small),
            ('a__large', large),
            ('b__large', large),
        ):
            write_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                trial,
                trial_config.AgentConfig(
                    import_path='x:Y',
                    kwargs={'system': system.name, 'fingerprint': 'fp-old'},
                ),
                task=f'f/{trial}',
            )

        drifted = jobs.drifted_systems(VOTES, [small, large])

        assert drifted == [
            ('acme-large', 'fp-old', fingerprint(large)),
            ('acme-small', 'fp-old', fingerprint(small)),
        ]


class TestJob:
    @pytest.mark.parametrize(
        ('status', 'reason', 'refusal'),
        [
            ('failed', None, None),
            ('incomplete', 'content_filter', None),
            ('completed', None, 'I cannot answer.'),
        ],
        ids=['failed', 'filtered', 'refused'],
    )
    def test_retries_infrastructure_failures_without_regenerating_bad_responses(
        self, status: str, reason: str | None, refusal: str | None
    ) -> None:
        result = ResponseResult(
            Response.model_validate(
                response(status=status, incomplete_reason=reason, refusal=refusal)
            ),
            'req-1',
        )
        config = jobs.job(VOTES, [make_system(api='openai-responses')], [])
        queue = RetryProbe(n_concurrent=1, retry_config=config.retry)

        with pytest.raises(NativeCompletionError) as error:
            result.check(complete=True)

        assert not queue.retries(type(error.value).__name__)
        assert queue.retries('APIConnectionError')
        assert queue.retries('InternalServerError')
        assert not queue.retries('AgentTimeoutError')
        assert not queue.retries('VerifierTimeoutError')

    def test_runs_each_case_system_through_generate_in_its_route_group(
        self, data_dir: Path
    ) -> None:
        large = make_system(prompt='Be brief.')
        small = make_system(name='small', route='acme/small', effort=None)
        task = data_dir / 'tasks' / 'f' / 'c'

        job = jobs.job(VOTES, [large, small], [task])

        assert [
            (agent.import_path, agent.model_name, agent.concurrency_group)
            for agent in job.agents
        ] == [
            ('assistant_bench.harnesses.generate:Generate', 'acme/large', 'acme/large'),
            ('assistant_bench.harnesses.generate:Generate', 'acme/small', 'acme/small'),
        ]
        assert job.agents[0].kwargs == {
            'api': 'openai-chat-completions',
            'system': 'acme-large',
            'fingerprint': fingerprint(large),
            'effort': 'medium',
            'system_prompt': 'Be brief.',
            'max_output_tokens': 64,
        }
        assert job.n_attempts == 2
        assert job.n_concurrent_trials == jobs.N_CONCURRENT_TRIALS
        assert job.retry.max_retries == 1
        assert job.verifier.disable
        assert job.environment == trial_config.EnvironmentConfig(
            import_path=HOST_ENVIRONMENT, kwargs={'auto_mounting': True}
        )
        assert job.jobs_dir == data_dir / 'jobs' / 'f'
        assert [t.path for t in job.tasks] == [task]

    def test_runs_a_task_family_under_its_harness_with_the_verifier(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        systems = jobs.family_systems(family, [make_system()])
        task = case_task(family, 'echo')

        job = jobs.job(family, systems, [task])

        (agent,) = job.agents
        assert (agent.import_path, agent.model_name, agent.concurrency_group) == (
            'assistant_bench.harnesses.terminus:Terminus',
            'acme/large',
            'acme/large',
        )
        assert agent.kwargs == {
            'api': 'openai-chat-completions',
            'system': 'acme-large',
            'fingerprint': fingerprint(systems[0]),
            'llm_call_kwargs': {'max_completion_tokens': 64},
            'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
            'reasoning_effort': 'medium',
            'record_terminal_session': False,
        }
        assert job.n_concurrent_trials == jobs.N_CONCURRENT_CONTAINERS
        assert not job.verifier.disable
        assert job.environment == trial_config.EnvironmentConfig(
            type=EnvironmentType.DOCKER
        )
        assert job.jobs_dir == data_dir / 'jobs' / 'f'
        assert [t.path for t in job.tasks] == [task]

    def test_keeps_a_successful_authored_task_reusable_at_its_declared_deadline(
        self, families_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        systems = jobs.family_systems(family, [make_system()])
        task = case_task(family, 'echo')
        monkeypatch.setattr('assistant_bench.tasks.AGENT_TIMEOUT_SEC', 900.0)
        old = jobs.job(family, systems, [task]).model_copy(
            update={'job_name': 'old-deadline'}
        )
        first = finish_job(old)
        monkeypatch.setattr('assistant_bench.tasks.AGENT_TIMEOUT_SEC', 3600.0)

        current = jobs.job(family, systems, [task])
        plan = asyncio.run(JobPlan.from_config(current))
        (source,) = current.source_jobs
        diff = compare_job_trials(plan, load_source_trials([(source, first)]))

        assert Task(task).config.agent.timeout_sec == 120.0
        assert current.agents == old.agents
        assert [(trial.task_name, trial.action) for trial in diff.trials] == [
            ('f/echo', DiffAction.REUSE)
        ]

    def test_diffs_against_the_earlier_jobs_of_the_family(self, data_dir: Path) -> None:
        jobs_dir = data_dir / 'jobs' / 'f'
        for name in ('job-1', 'job-2', 'job-3'):
            (jobs_dir / name).mkdir(parents=True)
        for name in ('job-1', 'job-3'):
            (jobs_dir / name / 'result.json').write_text('{}')

        job = jobs.job(VOTES, [make_system()], [])

        assert job.source_jobs == [
            SourceJobConfig(action='diff', type='local', path=jobs_dir / name)
            for name in ('job-1', 'job-3')
        ]

    def test_names_jobs_in_start_order_within_one_second(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        starts = iter(START + timedelta(milliseconds=ms) for ms in range(3))

        class Clock(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> Clock:
                return cls.fromtimestamp(next(starts).timestamp())

        monkeypatch.setattr(jobs, 'datetime', Clock)
        monkeypatch.setattr(job_config, 'datetime', Clock)

        names = [
            JobConfig().job_name,
            *(jobs.job(VOTES, [make_system()], []).job_name for _ in range(2)),
        ]

        assert names[0] < names[1] < names[2]


class TestCheckJob:
    def test_checks_the_committed_tasks_with_the_oracle_and_the_nop(
        self, data_dir: Path
    ) -> None:
        earlier = data_dir / 'checks' / 'smoke' / 'job-1'
        earlier.mkdir(parents=True)
        (earlier / 'result.json').touch()
        family = load_family('smoke')
        task = case_task(family, 'echo')

        job = jobs.check_job(family, [task])

        assert [(agent.name, agent.import_path) for agent in job.agents] == [
            ('oracle', None),
            ('nop', None),
        ]
        assert job.n_attempts == jobs.CHECK_ATTEMPTS
        assert job.n_concurrent_trials == jobs.N_CONCURRENT_CONTAINERS
        assert [t.path for t in job.tasks] == [task]
        assert not job.verifier.disable
        assert job.environment == trial_config.EnvironmentConfig(
            type=EnvironmentType.DOCKER
        )
        assert job.source_jobs == []
        assert job.jobs_dir == data_dir / 'checks' / 'smoke'


class TestPendingTrials:
    def test_counts_the_trials_harbor_would_rerun(self) -> None:
        family = load_family('smoke-text')
        large = make_system()
        small = make_system(name='small', route='acme/small')
        tasks = render_tasks(family, load_cases(family)[:2])
        finish_job(
            jobs.job(family, [large], tasks[:1]).model_copy(update={'job_name': 'old'})
        )

        pending = jobs.pending_trials(jobs.job(family, [large, small], tasks))

        assert pending == Counter({'acme-large': 1, 'small': 2})

    def test_counts_no_regrade_of_a_minor_bumped_task(self, families_dir: Path) -> None:
        family = voted_tasks(families_dir, 'again', 'echo')
        systems = jobs.family_systems(family, [make_system()])
        tasks = [case_task(family, task_id) for task_id in ('again', 'echo')]
        first = finish_job(jobs.job(family, systems, tasks))
        break_setting(tasks[1], ('task', 'version'), '1.1.0')
        job = jobs.job(family, systems, tasks)
        plan = asyncio.run(JobPlan.from_config(job))
        (source,) = job.source_jobs
        diff = compare_job_trials(plan, load_source_trials([(source, first)]))
        assert [trial.action for trial in diff.trials] == [
            DiffAction.REUSE,
            DiffAction.REGRADE,
        ]

        pending = jobs.pending_trials(job)

        assert pending == Counter()

    def test_counts_nothing_without_tasks(self) -> None:
        pending = jobs.pending_trials(jobs.job(VOTES, [make_system()], []))

        assert pending == Counter()


class TestHasJobs:
    @pytest.mark.parametrize(
        ('directory', 'result', 'expected'),
        [(False, False, False), (True, False, False), (True, True, True)],
        ids=['no-job', 'job-without-a-result', 'finished-job'],
    )
    def test_reports_whether_the_family_has_a_job(
        self, data_dir: Path, directory: bool, result: bool, expected: bool
    ) -> None:
        family = load_family('smoke-text')
        job = data_dir / 'jobs' / 'smoke-text' / 'job-1'
        if directory:
            job.mkdir(parents=True)
        if result:
            (job / 'result.json').touch()

        found = jobs.has_jobs(family)

        assert found is expected

    def test_ignores_a_check_job(self, data_dir: Path) -> None:
        job = data_dir / 'checks' / 'smoke' / 'job-1'
        job.mkdir(parents=True)
        (job / 'result.json').touch()

        found = jobs.has_jobs(load_family('smoke'))

        assert found is False


class TestRunJob:
    @staticmethod
    def stub_harbor(
        directory: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
    ) -> Path:
        """Install a recording Harbor CLI beside the fake interpreter."""
        harbor = directory / 'harbor'
        shutil.copyfile(FIXTURES / 'recording-harbor.sh', harbor)
        harbor.chmod(0o755)
        monkeypatch.setenv('STUB_HARBOR_EXIT_CODE', str(exit_code))
        environment = jobs.harbor_environment()
        monkeypatch.setattr(
            jobs,
            'harbor_environment',
            lambda: environment | {'STUB_HARBOR_EXIT_CODE': str(exit_code)},
        )
        monkeypatch.setattr(sys, 'executable', str(directory / 'python'))
        return harbor

    def test_checks_installed_context_before_downloads_or_harbor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = JobConfig(
            agents=[
                trial_config.AgentConfig(
                    import_path='assistant_bench.harnesses.installed:Codex',
                    kwargs={'version': '1.2.3'},
                )
            ]
        )
        engine = Mock()
        download = Mock()
        run = Mock()
        monkeypatch.setattr(
            jobs,
            'check_local_context',
            AsyncMock(side_effect=ValueError('context refused')),
        )
        monkeypatch.setattr(subprocess, 'check_output', engine)
        monkeypatch.setattr(jobs, 'artifact', download)
        monkeypatch.setattr(subprocess, 'run', run)

        with pytest.raises(ValueError, match='context refused'):
            jobs.run_job(config)

        engine.assert_not_called()
        download.assert_not_called()
        run.assert_not_called()

    def test_runs_harbor_on_the_config(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harbor = self.stub_harbor(tmp_path, monkeypatch, 0)
        family = load_family('smoke')
        job = jobs.check_job(family, [case_task(family, 'echo')])

        job_dir = jobs.run_job(job)

        args = Path(f'{harbor}.args').read_text().splitlines()
        assert args[:2] == ['run', '--config']
        assert args[3:] == ['--yes']
        assert JobConfig.model_validate_json(Path(f'{harbor}.json').read_text()) == job
        assert job_dir == data_dir / 'checks' / 'smoke' / job.job_name

    def test_raises_when_harbor_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.stub_harbor(tmp_path, monkeypatch, 3)
        family = load_family('smoke')
        job = jobs.check_job(family, [case_task(family, 'echo')])

        with pytest.raises(subprocess.CalledProcessError) as error:
            jobs.run_job(job)

        assert error.value.returncode == 3
