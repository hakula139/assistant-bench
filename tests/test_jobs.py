import asyncio
import shutil
import subprocess
import sys
import textwrap
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import httpx
import openai
import pytest
import tomli_w
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.agent.context import AgentContext
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.task import config as task_config
from harbor.models.task.task import Task
from harbor.models.trial import config as trial_config
from harbor.models.trial.result import TimingInfo, TrialResult
from harbor.trial.queue import TrialQueue
from openai.types.responses import Response

from assistant_bench import jobs
from assistant_bench.catalog import NixosConfigExport, System
from assistant_bench.diffs import workspace_diff
from assistant_bench.families import (
    Case,
    Family,
    Gate,
    Gates,
    VotesSettings,
    case_task,
    load_cases,
    load_family,
)
from assistant_bench.harnesses import HOST_ENVIRONMENT, fingerprint
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.harnesses.responses import ResponseResult
from assistant_bench.outputs import Output
from assistant_bench.paths import FAMILIES_DIR, data_dir as bench_data_dir
from assistant_bench.scoring.gates import failed_gates
from support import (
    finish_job,
    make_system,
    response,
    write_case_family,
    write_task_family,
    write_trial,
)


VOTES = Family(
    name='f',
    source='cases',
    settings=VotesSettings(
        scorer='votes',
        display_name='Family F',
        systems=['acme-large'],
        samples=2,
        reference='acme-large',
        categories=['reply'],
    ),
)
START = datetime(2026, 10, 21, tzinfo=UTC)
GREETING_DIFF = (
    'diff --git a/greeting.txt b/greeting.txt\nnew file mode 100644\n'
    '--- /dev/null\n+++ b/greeting.txt\n@@ -0,0 +1 @@\n+hello\n'
)


class RetryProbe(TrialQueue):
    def retries(self, name: str) -> bool:
        return self._should_retry_exception(name)


def votes_family(**settings: Any) -> Family:
    return VOTES.model_copy(
        update={'settings': VOTES.settings.model_copy(update=settings)}
    )


def current_agent(
    family: Family = VOTES, system: System | None = None
) -> trial_config.AgentConfig:
    resolved = jobs.family_systems(family, [system or make_system()])
    return jobs.job(family, resolved, []).agents[0]


def record_trial(
    job_dir: Path,
    name: str,
    agent: trial_config.AgentConfig,
    *,
    family: Family = VOTES,
    **fields: Any,
) -> UUID:
    if family.source == 'cases' and fields.get('instruction', 'Hi.') is not None:
        existing = {case.id: case for case in load_cases(family)}
        case_id = Path(fields['task']).name
        existing[case_id] = Case(
            id=case_id,
            category=family.settings.categories[0]
            if family.settings.categories
            else 'reply',
            source='synthetic',
            instruction=fields.get('instruction', 'Hi.'),
        )
        declare_cases(family, list(existing.values()))

    config = jobs.job(family, jobs.family_systems(family, [make_system()]), [])
    return write_trial(
        job_dir,
        name,
        agent,
        environment=config.environment,
        verifier_disabled=config.verifier.disable,
        **fields,
    )


def declare_cases(family: Family, cases: list[Case]) -> None:
    path = bench_data_dir() / 'cases' / f'{family.name}.toml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps({'cases': [case.model_dump() for case in cases]}))


def render_cases(family: Family, cases: list[Case]) -> list[Path]:
    declare_cases(family, cases)
    return jobs.render_tasks(family, cases)


def finish_generation(
    job: JobConfig, text: str, output_tokens: int | None = None
) -> None:
    """Write one finished generation with Harbor's planned config and lock."""
    job_dir = finish_job(job)
    (result_file,) = job_dir.glob('*/result.json')
    result = TrialResult.model_validate_json(result_file.read_text())
    result.agent_result = AgentContext(
        n_output_tokens=output_tokens, metadata={'finish_reason': 'stop'}
    )
    result_file.write_text(result.model_dump_json())
    agent_dir = result_file.parent / 'agent'
    agent_dir.mkdir()
    (agent_dir / OUTPUT_FILE).write_text(text)


def version(config: task_config.TaskConfig) -> str:
    assert config.task is not None
    assert config.task.version is not None
    return config.task.version


def installed_version(path: Path) -> str:
    return version(Task(path, disable_verification=True).config)


class TestRenderTaskConfig:
    def test_pins_the_version_of_a_known_task(self) -> None:
        config = jobs.render_task_config('smoke-text/en-greeting', 'Hi.')

        assert config.agent.timeout_sec == 3600.0
        assert version(config) == '1.0.0+8e49aab92cda'

    def test_changes_the_version_with_the_agent_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = version(jobs.render_task_config('f/a', 'Hi.'))
        monkeypatch.setattr(jobs, 'AGENT_TIMEOUT_SEC', 60.0)

        after = version(jobs.render_task_config('f/a', 'Hi.'))

        assert after != before


class TestRenderTasks:
    def test_writes_a_versioned_task(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        (case, _) = load_cases(family)

        (path,) = jobs.render_tasks(family, [case])

        assert path == data_dir / 'tasks' / 'smoke-text' / 'en-greeting'
        assert installed_version(path) == version(
            jobs.render_task_config('smoke-text/en-greeting', case.instruction)
        )
        assert Task(path, disable_verification=True).instruction == case.instruction

    def test_reruns_an_edited_case_and_reuses_an_unedited_one(
        self, data_dir: Path
    ) -> None:
        family = load_family('smoke-text')
        cases = load_cases(family)
        first = finish_job(
            jobs.job(family, [make_system()], jobs.render_tasks(family, cases))
        )
        edited = [cases[0].model_copy(update={'instruction': 'Reworded.'}), cases[1]]

        job = jobs.job(family, [make_system()], jobs.render_tasks(family, edited))
        plan = asyncio.run(JobPlan.from_config(job))
        (source,) = job.source_jobs
        diff = compare_job_trials(plan, load_source_trials([(source, first)]))

        assert {trial.task_name: trial.action for trial in diff.trials} == {
            'smoke-text/en-greeting': DiffAction.RERUN,
            'smoke-text/zh-greeting': DiffAction.REUSE,
        }

    def test_reruns_a_successful_case_at_the_new_deadline_then_reuses_its_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        system = make_system()
        family = with_settings(
            load_family('smoke-text'), systems=[system.name], reference=system.name
        )
        cases = load_cases(family)[:1]
        monkeypatch.setattr(jobs, 'AGENT_TIMEOUT_SEC', 900.0)
        old = jobs.job(family, [system], jobs.render_tasks(family, cases)).model_copy(
            update={'job_name': 'old-deadline'}
        )
        finish_generation(old, 'Earlier success.')
        monkeypatch.setattr(jobs, 'AGENT_TIMEOUT_SEC', 3600.0)

        new = jobs.job(family, [system], jobs.render_tasks(family, cases)).model_copy(
            update={'job_name': 'new-deadline'}
        )
        plan = asyncio.run(JobPlan.from_config(new))
        diff = compare_job_trials(
            plan,
            load_source_trials(
                [
                    (source, source.path)
                    for source in new.source_jobs
                    if source.path is not None
                ]
            ),
        )

        assert [(trial.task_name, trial.action) for trial in diff.trials] == [
            ('smoke-text/en-greeting', DiffAction.RERUN)
        ]
        finish_generation(new, 'Success at the current deadline.')

        rerun = jobs.job(family, [system], jobs.render_tasks(family, cases))
        plan = asyncio.run(JobPlan.from_config(rerun))
        diff = compare_job_trials(
            plan,
            load_source_trials(
                [
                    (source, source.path)
                    for source in rerun.source_jobs
                    if source.path is not None
                ]
            ),
        )

        assert [(trial.task_name, trial.action) for trial in diff.trials] == [
            ('smoke-text/en-greeting', DiffAction.REUSE)
        ]
        (output,) = jobs.collect(family, {system.name: system})
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

        jobs.render_tasks(family, load_cases(family))

        assert sorted(
            path.name for path in (data_dir / 'tasks' / 'smoke-text').iterdir()
        ) == ['en-greeting', 'zh-greeting']


def voted_tasks(families_dir: Path, *task_ids: str) -> Family:
    """Write task family `f`, scored by votes, that runs `acme-large` as terminus-2."""
    write_task_family(families_dir, *task_ids, systems=('acme-large',))
    return load_family('f')


def with_settings(family: Family, **settings: Any) -> Family:
    return family.model_copy(
        update={'settings': family.settings.model_copy(update=settings)}
    )


class TestCheckSystems:
    @pytest.mark.parametrize(
        ('harness', 'system', 'message'),
        [
            pytest.param(
                'terminus-2',
                make_system(vendor=None),
                'Family `f`: set vendor metadata for `acme-large` in the model '
                'catalog or their `route` tables in `systems.toml`.',
                id='missing-vendor',
            ),
            pytest.param(
                'claude-code',
                make_system(),
                'Family `f`: harness `claude-code` is not one of `generate`, '
                '`terminus-2`.',
                id='unknown-harness',
            ),
            pytest.param(
                'generate',
                make_system(),
                'Family `f`: harness `generate` makes one request and never acts '
                "in a task's environment, so choose among `terminus-2`.",
                id='single-call-harness',
            ),
            pytest.param(
                'terminus-2',
                make_system(prompt='Be brief.'),
                'Family `f` lists `acme-large`, whose prompt the `terminus-2` '
                'harness cannot send. Remove them from `systems`.',
                id='unsent-prompt',
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
            pytest.param(VOTES, make_system(vendor=None), id='legacy-case-system'),
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

        with pytest.raises(openai.APIError) as error:
            result.check(
                httpx.Request('POST', 'http://127.0.0.1/v1/responses'), complete=True
            )

        assert not queue.retries(type(error.value).__name__)
        assert not queue.retries('MessageCompletionError')
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
            import_path=HOST_ENVIRONMENT
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
            'openai/acme/large',
            'acme/large',
        )
        assert agent.kwargs == {
            'use_responses_api': False,
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
        monkeypatch.setattr(jobs, 'AGENT_TIMEOUT_SEC', 900.0)
        old = jobs.job(family, systems, [task]).model_copy(
            update={'job_name': 'old-deadline'}
        )
        first = finish_job(old)
        monkeypatch.setattr(jobs, 'AGENT_TIMEOUT_SEC', 3600.0)

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


class TestChangedTasks:
    @pytest.mark.parametrize(
        ('edit', 'bump', 'expected'),
        [
            pytest.param(False, None, [], id='unedited'),
            pytest.param(True, None, [('echo', '1.0.0')], id='edited-without-a-bump'),
            pytest.param(True, '1.0.1', [], id='edited-with-a-bump'),
        ],
    )
    def test_names_a_task_edited_under_a_version_a_trial_recorded(
        self,
        families_dir: Path,
        edit: bool,
        bump: str | None,
        expected: list[tuple[str, str]],
    ) -> None:
        family = voted_tasks(families_dir, 'again', 'echo')
        tasks = [case_task(family, task_id) for task_id in ('again', 'echo')]
        finish_job(
            jobs.job(family, jobs.family_systems(family, [make_system()]), tasks)
        )
        retired = tasks[0] / 'tests' / 'test.sh'
        retired.write_text(retired.read_text() + '# Edited.\n')
        test = tasks[1] / 'tests' / 'test.sh'
        if edit:
            test.write_text(test.read_text() + '# Edited.\n')
        if bump:
            break_setting(tasks[1], ('task', 'version'), bump)

        changed = jobs.changed_tasks(family, tasks[1:])

        assert changed == expected


def task_family(families_dir: Path, *task_ids: str) -> tuple[Family, list[Path]]:
    """Write task family `f` with a copy of the smoke echo task per ID."""
    write_task_family(families_dir, *task_ids)
    family = load_family('f')
    return family, [case_task(family, task_id) for task_id in task_ids]


def break_setting(task: Path, path: tuple[str, ...], value: Any) -> None:
    """Set or remove one setting in a task's `task.toml`."""
    config_path = task / 'task.toml'
    config = tomllib.loads(config_path.read_text())
    target = config
    for key in path[:-1]:
        target = target[key]
    if value is None:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = value
    config_path.write_text(tomli_w.dumps(config))


class TestCheckTasks:
    def test_accepts_the_committed_smoke_task(self) -> None:
        family = load_family('smoke')

        jobs.check_tasks(family, [case_task(family, 'echo')])

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
            jobs.check_tasks(family, tasks)

        assert f'`tasks/echo/task.toml`: {message}' in str(error.value)

    def test_accepts_a_string_artifact(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('artifacts',), ['/app'])

        jobs.check_tasks(family, tasks)

    def test_names_a_problem_in_a_later_task(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo', 'again')
        break_setting(tasks[1], ('environment', 'cpus'), None)

        with pytest.raises(ValueError) as error:
            jobs.check_tasks(family, tasks)

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
            jobs.check_tasks(load_family('smoke'), [task])

        assert '`cases/smoke/echo/task.toml`: set `[environment] cpus`.' in str(
            error.value
        )

    def test_names_every_problem_of_a_task(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('environment', 'cpus'), None)
        break_setting(tasks[0], ('environment', 'memory_mb'), None)

        with pytest.raises(ValueError) as error:
            jobs.check_tasks(family, tasks)

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
            jobs.check_tasks(family, tasks)

        assert str(error.value).startswith(
            'Family `f` has tasks to fix:\n`tasks/echo/task.toml`: '
            '`environment.cpus`: Input should be a valid integer'
        )


class TestAuthorVendors:
    @pytest.mark.parametrize(
        ('author', 'vendor', 'systems'),
        [
            ('retired-author', 'acme', {}),
            ('writer', 'other', {'writer': make_system(name='writer', vendor='other')}),
        ],
        ids=['catalog-vendor-with-retired-label', 'explicit-bare-vendor'],
    )
    def test_resolves_provenance_without_requiring_a_runnable_author(
        self,
        families_dir: Path,
        export: NixosConfigExport,
        author: str,
        vendor: str,
        systems: dict[str, System],
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        task = case_task(family, 'echo')
        break_setting(task, ('metadata', 'author'), author)
        break_setting(task, ('metadata', 'author_vendor'), vendor)

        vendors = jobs.author_vendors(family, [task], systems, export)

        assert vendors == {'echo': vendor}

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
            jobs.author_vendors(family, [task], {}, export)

        assert str(error.value).startswith(
            f'Family `f` has authors to fix:\n`tasks/echo/task.toml`: {message}'
        )


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
        assert job.n_attempts == jobs.CHECK_ATTEMPTS == 3
        assert job.n_concurrent_trials == jobs.N_CONCURRENT_CONTAINERS
        assert [t.path for t in job.tasks] == [task]
        assert not job.verifier.disable
        assert job.environment == trial_config.EnvironmentConfig(
            type=EnvironmentType.DOCKER
        )
        assert job.source_jobs == []
        assert job.jobs_dir == data_dir / 'checks' / 'smoke'


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
        """Put a `harbor` beside the interpreter that records its call and exits.

        It writes its arguments and the config it receives beside itself.
        """
        harbor = directory / 'harbor'
        harbor.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\\n' "$@" > "$0.args"
                cp "$3" "$0.json"
                exit {exit_code}
                """
            )
        )
        harbor.chmod(0o755)
        monkeypatch.setattr(sys, 'executable', str(directory / 'python'))
        return harbor

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


class TestCollect:
    @pytest.fixture(autouse=True)
    def current_sources(self, families_dir: Path, data_dir: Path) -> None:
        write_case_family(families_dir, ['acme-large'], name='smoke-text')
        write_case_family(families_dir, ['acme-large'], name='f')
        cases = data_dir / 'cases' / 'smoke-text.toml'
        cases.parent.mkdir(parents=True)
        cases.write_text((FAMILIES_DIR / 'smoke-text' / 'cases.toml').read_text())

    @pytest.mark.parametrize('deleted', [False, True], ids=['edited', 'removed'])
    def test_excludes_stale_case_sources_without_rerendering_installed_tasks(
        self, data_dir: Path, deleted: bool
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        original = Case(id='a', category='reply', source='synthetic', instruction='Hi.')
        tasks = render_cases(family, [original])
        config = jobs.job(family, [system], tasks)
        finish_generation(
            config.model_copy(update={'job_name': 'job-1'}), 'Old answer.'
        )
        current = original.model_copy(update={'instruction': 'Write a new answer.'})
        declare_cases(family, [] if deleted else [current])

        outputs = jobs.collect(family, {system.name: system})

        assert outputs == []
        assert (tasks[0] / 'instruction.md').read_text() == 'Hi.'

    def test_keeps_only_trials_of_the_current_task(self, data_dir: Path) -> None:
        family = with_settings(
            load_family('smoke-text'), systems=['a', 'b'], reference='a'
        )
        job = data_dir / 'jobs' / 'smoke-text' / 'job-1'
        systems = {side: make_system(name=side) for side in ('a', 'b')}
        agents = {
            side: current_agent(family, system) for side, system in systems.items()
        }
        record_trial(
            job,
            'en-greeting__a-old',
            agents['a'],
            task='smoke-text/en-greeting',
            output='old',
            version=jobs.TASK_VERSION,
            family=family,
        )
        for case, side in (
            ('en-greeting', 'b'),
            ('zh-greeting', 'a'),
            ('zh-greeting', 'b'),
        ):
            record_trial(
                job,
                f'{case}__{side}',
                agents[side],
                task=f'smoke-text/{case}',
                output=f'{case} {side}',
                context=AgentContext(metadata={'finish_reason': 'stop'}),
                family=family,
            )

        (data_dir / 'tasks' / 'smoke-text' / 'stray.md').write_text('Stray.')

        outputs = jobs.collect(family, systems)

        assert {(output.case, output.system) for output in outputs} == {
            ('en-greeting', 'b'),
            ('zh-greeting', 'a'),
            ('zh-greeting', 'b'),
        }

    def test_skips_a_trial_whose_case_no_longer_has_a_task(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1, gates=Gates(script='hans'))
        cases = [
            Case(id='a', category='reply', source='held-out', instruction='Hi.'),
            Case(id='b', category='reply', source='held-out', instruction='Hi.'),
        ]
        for path in render_cases(family, cases):
            record_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{path.name}__1',
                current_agent(family),
                task=f'f/{path.name}',
                output='你好。',
                instruction=None,
                context=AgentContext(metadata={'finish_reason': 'stop'}),
                family=family,
            )
        render_cases(family, cases[:1])

        (output,) = jobs.collect(family, {'acme-large': make_system()})

        assert output.case == 'a'

    def test_keeps_a_trial_whose_instruction_has_a_carriage_return(
        self, data_dir: Path
    ) -> None:
        family = load_family('smoke-text')
        case = Case(
            id='en-greeting',
            category='message',
            source='held-out',
            instruction='Line one.\r\nLine two.',
        )
        (path,) = render_cases(family, [case])
        record_trial(
            data_dir / 'jobs' / 'smoke-text' / 'job-1',
            'en-greeting__1',
            current_agent(family),
            task='smoke-text/en-greeting',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )

        (output,) = jobs.collect(family, {'acme-large': make_system()})

        assert output.case == path.name

    def test_excludes_trials_after_a_generation_timeout_changes(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        family = load_family('smoke-text')
        render_cases(family, load_cases(family)[:1])
        record_trial(
            data_dir / 'jobs' / 'smoke-text' / 'job-1',
            'en-greeting__1',
            current_agent(family),
            task='smoke-text/en-greeting',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )
        monkeypatch.setattr(jobs, 'AGENT_TIMEOUT_SEC', 60.0)

        outputs = jobs.collect(family, {'acme-large': make_system()})

        assert outputs == []

    @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
    def test_collects_generations_and_errors(self, data_dir: Path, api: str) -> None:
        job_dir = data_dir / 'jobs' / 'f' / 'job-1'
        system = make_system(api=api)
        generate = current_agent(VOTES, system)
        record_trial(
            job_dir,
            'b__1',
            generate,
            task='f/b',
            output='Hi.',
            context=AgentContext(
                n_input_tokens=1_000,
                n_cache_tokens=600,
                n_output_tokens=500,
                cost_usd=0.0,
                metadata={
                    'reasoning_tokens': 100,
                    'latency_sec': 1.5,
                    'finish_reason': 'stop',
                    'response_model': 'large-2026',
                    'request_id': 'req-1',
                    'nixos_config_rev': 'rev-a',
                },
            ),
            family=VOTES,
        )
        record_trial(
            job_dir, 'a__1', generate, task='f/a', error='TimeoutError', family=VOTES
        )

        outputs = jobs.collect(VOTES, {system.name: system})

        assert [(output.case, output.error) for output in outputs] == [
            ('a', 'TimeoutError'),
            ('b', None),
        ]
        assert (outputs[0].cost_usd, outputs[0].gate_failures) == (None, None)
        assert outputs[1].model_dump(exclude={'case', 'error'}) == {
            'trial': 'b__1',
            'job': 'job-1',
            'system': 'acme-large',
            'fingerprint': fingerprint(system),
            'nixos_config_rev': 'rev-a',
            'harness_version': None,
            'text': 'Hi.',
            'reward': None,
            'gate_failures': [],
            'finish_reason': 'stop',
            'stop_details': None,
            'input_tokens': 1_000,
            'cached_tokens': 600,
            'cache_write_tokens': None,
            'output_tokens': 500,
            'reasoning_tokens': 100,
            'cost_usd': pytest.approx((400 * 2.0 + 600 * 0.2 + 500 * 10.0) / 1e6),
            'latency_sec': 1.5,
            'response_model': 'large-2026',
            'request_id': 'req-1',
        }
        written = (data_dir / 'outputs' / 'f.jsonl').read_text().splitlines()
        assert [Output.model_validate_json(line) for line in written] == outputs

    def test_prefers_a_trial_that_sent_the_current_request(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1)
        system = make_system(max_output_tokens=300)
        tasks = render_cases(
            family,
            [Case(id='a', category='reply', source='held-out', instruction='Hi.')],
        )
        for job, tokens, output in (
            ('2026-10-20__10-00-00', 600, 'old request'),
            ('2026-10-21__10-00-00', 300, 'current request'),
        ):
            config = jobs.job(
                family, [system.model_copy(update={'max_output_tokens': tokens})], tasks
            )
            finish_generation(
                config.model_copy(update={'job_name': job}), output, tokens
            )

        outputs = jobs.collect(family, {system.name: system})

        assert [
            (output.job, output.text, output.output_tokens) for output in outputs
        ] == [('2026-10-21__10-00-00', 'current request', 300)]

    @pytest.mark.parametrize(
        ('section', 'changes'),
        [
            pytest.param(
                'environment',
                {'type': EnvironmentType.DOCKER, 'import_path': None},
                id='environment-type',
            ),
            pytest.param(
                'environment', {'kwargs': {'legacy': True}}, id='environment-kwargs'
            ),
            pytest.param('agent', {'model_name': 'acme/old'}, id='agent-route'),
            pytest.param(
                'agent', {'import_path': 'legacy:Generate'}, id='agent-import'
            ),
            pytest.param('verifier', {'disable': False}, id='verifier-enabled'),
        ],
    )
    def test_prefers_a_trial_with_the_current_execution_config(
        self,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        section: str,
        changes: dict[str, Any],
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        tasks = render_cases(
            family,
            [Case(id='a', category='reply', source='held-out', instruction='Hi.')],
        )
        current = jobs.job(family, [system], tasks)
        if section == 'environment':
            old = current.model_copy(
                update={'environment': current.environment.model_copy(update=changes)}
            )
        elif section == 'verifier':
            old = current.model_copy(
                update={'verifier': current.verifier.model_copy(update=changes)}
            )
        else:
            old = current.model_copy(
                update={'agents': [current.agents[0].model_copy(update=changes)]}
            )
        finish_generation(old.model_copy(update={'job_name': 'job-1'}), 'old config')
        finish_generation(
            current.model_copy(update={'job_name': 'job-2'}), 'current config'
        )
        gates = Mock(wraps=failed_gates)
        monkeypatch.setattr(jobs, 'failed_gates', gates)

        (output,) = jobs.collect(family, {system.name: system})

        assert (output.job, output.text) == ('job-2', 'current config')
        gates.assert_called_once_with(family.settings, 'current config', 'stop', 'Hi.')

        shutil.rmtree(data_dir / 'jobs' / 'f' / 'job-2')

        assert jobs.collect(family, {system.name: system}) == []

    def test_keeps_the_oldest_trial_that_sent_the_current_request(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        tasks = render_cases(
            family,
            [Case(id='a', category='reply', source='held-out', instruction='Hi.')],
        )
        config = jobs.job(family, [system], tasks)
        for job, output in (
            ('2026-10-20__10-00-00', 'first'),
            ('2026-10-21__10-00-00', 'second'),
        ):
            finish_generation(config.model_copy(update={'job_name': job}), output)

        outputs = jobs.collect(family, {system.name: system})

        assert [(output.job, output.text) for output in outputs] == [
            ('2026-10-20__10-00-00', 'first')
        ]

    def test_keeps_an_older_success_over_a_newer_error(self, data_dir: Path) -> None:
        family = votes_family(samples=1)
        system = make_system()
        jobs_dir = data_dir / 'jobs' / 'f'
        record_trial(
            jobs_dir / '2026-10-20__10-00-00',
            'a__1',
            current_agent(family, system),
            task='f/a',
            output='kept',
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )
        record_trial(
            jobs_dir / '2026-10-21__10-00-00',
            'a__2',
            current_agent(family, system),
            task='f/a',
            error='TimeoutError',
            family=family,
        )

        outputs = jobs.collect(family, {system.name: system})

        assert [(output.job, output.error, output.text) for output in outputs] == [
            ('2026-10-20__10-00-00', None, 'kept')
        ]

    def test_excludes_an_old_success_even_when_the_current_request_failed(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        jobs_dir = data_dir / 'jobs' / 'f'
        record_trial(
            jobs_dir / '2026-10-20__10-00-00',
            'a__1',
            current_agent(family, system),
            task='f/a',
            error='TimeoutError',
            family=family,
        )
        record_trial(
            jobs_dir / '2026-10-21__10-00-00',
            'a__2',
            current_agent(family, system.model_copy(update={'max_output_tokens': 600})),
            task='f/a',
            output='old request',
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )

        outputs = jobs.collect(family, {system.name: system})

        assert [(output.job, output.error, output.text) for output in outputs] == [
            ('2026-10-20__10-00-00', 'TimeoutError', None)
        ]

    def test_keeps_a_rerun_success_over_the_error_it_replaced(
        self, data_dir: Path
    ) -> None:
        system = make_system()
        family = votes_family(samples=1)
        generate = current_agent(family, system)
        jobs_dir = data_dir / 'jobs' / 'f'
        record_trial(
            jobs_dir / 'job-1',
            'a__1',
            generate,
            task='f/a',
            error='TimeoutError',
            family=family,
        )
        record_trial(
            jobs_dir / 'job-2',
            'a__2',
            generate,
            task='f/a',
            output='Hi.',
            family=family,
        )

        outputs = jobs.collect(family, {system.name: system})

        assert [
            (output.job, output.case, output.error, output.text) for output in outputs
        ] == [('job-2', 'a', None, 'Hi.')]

    @pytest.mark.parametrize(
        'change',
        [
            pytest.param({'name': 'old-name'}, id='renamed'),
            pytest.param({'route': 'acme/old'}, id='new-fingerprint'),
            pytest.param({'api': 'openai-responses'}, id='changed-transport'),
        ],
    )
    def test_excludes_a_previous_system_identity(
        self, data_dir: Path, change: dict[str, str]
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        previous = system.model_copy(update=change)
        for job, recorded in (('job-1', previous), ('job-2', system)):
            record_trial(
                data_dir / 'jobs' / 'f' / job,
                'a__1',
                current_agent(family, recorded),
                task='f/a',
                output=job,
                family=family,
            )

        outputs = jobs.collect(family, {system.name: system})

        assert [
            (output.system, output.fingerprint, output.text) for output in outputs
        ] == [(system.name, fingerprint(system), 'job-2')]

    def test_gates_each_output_against_its_case_instruction(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1, language='zh', gates=Gates(script='hans'))
        generate = current_agent(family)
        stop = AgentContext(metadata={'finish_reason': 'stop'})
        for case, output in (('a', '臺北今天很热。'), ('b', '臺北今天很熱。')):
            record_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{case}__1',
                generate,
                task=f'f/{case}',
                output=output,
                instruction='介绍一下臺北的天气。',
                context=stop,
                family=family,
            )

        outputs = jobs.collect(family, {'acme-large': make_system()})

        assert [(output.case, output.gate_failures) for output in outputs] == [
            ('a', []),
            ('b', [Gate.SCRIPT]),
        ]

    def test_reads_no_instruction_without_the_script_gate(self, data_dir: Path) -> None:
        declare_cases(
            VOTES,
            [Case(id='a', category='reply', source='synthetic', instruction='Hi.')],
        )
        task = data_dir / 'tasks' / 'f' / 'a'
        task.mkdir(parents=True)
        (task / 'task.toml').write_text(
            jobs.render_task_config('f/a', 'Hi.').model_dump_toml()
        )
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(VOTES),
            task='f/a',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=VOTES,
        )

        (output,) = jobs.collect(VOTES, {'acme-large': make_system()})

        assert output.gate_failures == []

    def test_reads_the_reward_only_for_a_task_family(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_task_family(families_dir, 'a', name='g', systems=('acme-large',))
        task_family = load_family('g')
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(VOTES),
            task='f/a',
            reward=1.0,
            family=VOTES,
        )
        record_trial(
            data_dir / 'jobs' / 'g' / 'job-1',
            'a__1',
            current_agent(task_family),
            task='g/a',
            reward=1.0,
            instruction=None,
            version='1.0.0',
            workspace={'greeting.txt': 'hello'},
            family=task_family,
        )

        (scored,) = jobs.collect(task_family, {'acme-large': make_system()})
        (voted,) = jobs.collect(VOTES, {'acme-large': make_system()})

        assert (scored.case, scored.system, scored.reward) == ('a', 'acme-large', 1.0)
        assert scored.gate_failures == []
        assert voted.reward is None

    @pytest.mark.parametrize(
        ('current', 'kept'),
        [
            pytest.param('1.0.0', True, id='same-version'),
            pytest.param('1.0.3', True, id='patch-bump'),
            pytest.param('1.0.3-rc.1', True, id='patch-bump-to-a-pre-release'),
            pytest.param('1.0.0-rc.1', False, id='pre-release-alone'),
            pytest.param('1.0.0+build.2', False, id='build-metadata-alone'),
            pytest.param('1.1.0', False, id='minor-bump'),
            pytest.param('2.0.0', False, id='major-bump'),
        ],
    )
    def test_keeps_trials_of_current_tasks_at_a_version_harbor_reuses(
        self, families_dir: Path, data_dir: Path, current: str, kept: bool
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        break_setting(case_task(family, 'echo'), ('task', 'version'), current)
        for task in ('echo', 'deleted'):
            record_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{task}__1',
                current_agent(family),
                task=f'f/{task}',
                reward=1.0,
                instruction=None,
                version='1.0.0',
                family=family,
            )

        outputs = jobs.collect(family, {'acme-large': make_system()})

        assert [output.case for output in outputs] == (['echo'] if kept else [])

    @pytest.mark.parametrize(
        ('reward', 'error', 'verdict', 'diff'),
        [
            pytest.param(1.0, None, [], True, id='passed'),
            pytest.param(0.0, None, [Gate.TESTS], True, id='failed'),
            pytest.param(0.5, None, [Gate.TESTS], True, id='partial'),
            pytest.param(1.0, 'AgentTimeoutError', [], True, id='graded-timeout'),
            pytest.param(None, 'RuntimeError', None, False, id='never-graded'),
        ],
    )
    def test_gates_a_voted_task_by_its_tests_and_diffs_its_workspace(
        self,
        families_dir: Path,
        data_dir: Path,
        reward: float | None,
        error: str | None,
        verdict: list[Gate] | None,
        diff: bool,
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        base = case_task(family, 'echo') / 'environment' / 'app'
        base.mkdir()
        (base / 'README.md').write_text('Echo.\n')
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__1',
            current_agent(family),
            task='f/echo',
            reward=reward,
            error=error,
            instruction=None,
            version='1.0.0',
            workspace={'README.md': 'Echo.\n', 'greeting.txt': 'hello\n'},
            family=family,
        )

        (output,) = jobs.collect(family, {'acme-large': make_system()})

        assert (output.error, output.reward, output.gate_failures) == (
            error,
            reward,
            verdict,
        )
        assert output.text == (GREETING_DIFF if diff else None)

    @pytest.mark.parametrize(
        ('attempts', 'job', 'error', 'reward', 'verdict'),
        [
            pytest.param(
                [
                    (None, 'InternalServerError'),
                    (None, 'CancelledError'),
                    (1.0, 'AgentTimeoutError'),
                ],
                'job-3',
                'AgentTimeoutError',
                1.0,
                [],
                id='graded-error-over-ungraded-errors',
            ),
            pytest.param(
                [
                    (0.5, 'AgentTimeoutError'),
                    (1.0, 'AgentTimeoutError'),
                    (0.0, 'AgentTimeoutError'),
                ],
                'job-1',
                'AgentTimeoutError',
                0.5,
                [Gate.TESTS],
                id='oldest-graded-error',
            ),
            pytest.param(
                [(0.0, 'AgentTimeoutError'), (0.0, None)],
                'job-2',
                None,
                0.0,
                [Gate.TESTS],
                id='success-over-graded-error',
            ),
        ],
    )
    def test_ranks_a_success_then_a_graded_then_an_ungraded_error_oldest_first(
        self,
        families_dir: Path,
        data_dir: Path,
        attempts: list[tuple[float | None, str | None]],
        job: str,
        error: str | None,
        reward: float,
        verdict: list[Gate],
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        agent = current_agent(family)
        for index, (attempt_reward, attempt_error) in enumerate(attempts, start=1):
            record_trial(
                data_dir / 'jobs' / 'f' / f'job-{index}',
                f'echo__{index}',
                agent,
                task='f/echo',
                reward=attempt_reward,
                error=attempt_error,
                instruction=None,
                version='1.0.0',
                workspace={'greeting.txt': 'hello\n'},
                family=family,
            )

        (output,) = jobs.collect(family, {'acme-large': make_system()})

        assert (
            output.job,
            output.error,
            output.reward,
            output.gate_failures,
        ) == (job, error, reward, verdict)

    @pytest.mark.parametrize(
        ('regrades', 'verdict'),
        [
            pytest.param(1, [], id='regrade'),
            pytest.param(2, [Gate.TESTS], id='regrade-of-a-regrade'),
        ],
    )
    def test_gives_a_regrade_the_output_of_the_trial_that_wrote_its_solution(
        self,
        families_dir: Path,
        data_dir: Path,
        regrades: int,
        verdict: list[Gate],
    ) -> None:
        rewards = [0.0, 1.0, 0.5]
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        agent = current_agent(family, system)
        origin = data_dir / 'jobs' / 'f' / 'job-1'
        source = record_trial(
            origin,
            'echo__origin',
            agent,
            task='f/echo',
            reward=rewards[0],
            instruction=None,
            version='1.0.0',
            workspace={'greeting.txt': 'hello\n'},
            context=AgentContext(
                n_input_tokens=1_000,
                n_output_tokens=500,
                metadata={'harbor_version': '0.24.0'},
            ),
            agent_execution=TimingInfo(
                started_at=START, finished_at=START + timedelta(seconds=30)
            ),
            family=family,
        )
        (origin / 'echo__origin' / 'artifacts' / 'app' / 'link').symlink_to(
            'greeting.txt'
        )
        for minor in range(1, regrades + 1):
            # Harbor's regrade copies the artifacts with symlinks dereferenced.
            source = record_trial(
                data_dir / 'jobs' / 'f' / f'job-{minor + 1}',
                f'echo__regrade-{minor}',
                agent,
                task='f/echo',
                reward=rewards[minor],
                instruction=None,
                version=f'1.{minor}.0',
                workspace={'greeting.txt': 'hello\n', 'link': 'hello\n'},
                context=AgentContext(n_input_tokens=1, n_output_tokens=1),
                source=source,
                family=family,
            )
        break_setting(case_task(family, 'echo'), ('task', 'version'), f'1.{regrades}.0')

        (output,) = jobs.collect(family, {system.name: system})

        assert output.model_dump(
            include={
                'job',
                'trial',
                'text',
                'input_tokens',
                'cost_usd',
                'latency_sec',
                'harness_version',
                'reward',
                'gate_failures',
            }
        ) == {
            'job': 'job-1',
            'trial': 'echo__origin',
            'text': GREETING_DIFF + 'diff --git a/link b/link\nnew file mode 120000\n'
            '--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+greeting.txt\n'
            '\\ No newline at end of file\n',
            'input_tokens': 1_000,
            'cost_usd': pytest.approx((1_000 * 2.0 + 500 * 10.0) / 1e6),
            'latency_sec': 30.0,
            'harness_version': '0.24.0',
            'reward': rewards[regrades],
            'gate_failures': verdict,
        }

    @pytest.mark.parametrize('changed', ['agent', 'environment'])
    def test_regrading_cannot_turn_an_old_generation_contract_into_a_current_one(
        self, families_dir: Path, data_dir: Path, changed: str
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        config = jobs.job(family, jobs.family_systems(family, [system]), [])
        agent = config.agents[0]
        old_agent = (
            agent.model_copy(update={'kwargs': agent.kwargs | {'max_turns': 40}})
            if changed == 'agent'
            else agent
        )
        old_environment = (
            config.environment.model_copy(update={'force_build': True})
            if changed == 'environment'
            else config.environment
        )
        source = write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__origin',
            old_agent,
            task='f/echo',
            reward=0.0,
            instruction=None,
            version='1.0.0',
            workspace={'greeting.txt': 'hello\n'},
            environment=old_environment,
            verifier_disabled=config.verifier.disable,
        )
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-2',
            'echo__regrade',
            agent,
            task='f/echo',
            reward=1.0,
            instruction=None,
            version='1.1.0',
            workspace={'greeting.txt': 'hello\n'},
            source=source,
            family=family,
        )
        break_setting(case_task(family, 'echo'), ('task', 'version'), '1.1.0')

        outputs = jobs.collect(family, {system.name: system})

        assert outputs == []

    @pytest.mark.parametrize(
        ('source_known', 'expected'),
        [
            pytest.param(
                True,
                ('job-1', 'echo__origin', 'VerifierTimeoutError', None, None),
                id='errored-regrade',
            ),
            pytest.param(
                False,
                ('job-2', 'echo__regrade', 'VerifierTimeoutError', None, None),
                id='source-missing-from-the-index',
            ),
        ],
    )
    def test_takes_a_regrade_error_and_ends_its_chain_at_a_missing_source(
        self,
        families_dir: Path,
        data_dir: Path,
        source_known: bool,
        expected: tuple[str, str, str, float | None, list[Gate] | None],
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        agent = current_agent(family)
        source = record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__origin',
            agent,
            task='f/echo',
            reward=1.0,
            instruction=None,
            version='1.0.0',
            family=family,
        )
        if not source_known:
            shutil.rmtree(data_dir / 'jobs' / 'f' / 'job-1')
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-2',
            'echo__regrade',
            agent,
            task='f/echo',
            error='VerifierTimeoutError',
            instruction=None,
            version='1.1.0',
            source=source,
            family=family,
        )
        break_setting(case_task(family, 'echo'), ('task', 'version'), '1.1.0')

        (output,) = jobs.collect(family, {'acme-large': make_system()})

        assert (
            output.job,
            output.trial,
            output.error,
            output.reward,
            output.gate_failures,
        ) == expected

    def test_keeps_one_output_for_a_solution_harbor_regraded_twice(
        self,
        families_dir: Path,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        family = with_settings(voted_tasks(families_dir, 'echo'), samples=2)
        agent = current_agent(family)
        origin = record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__origin',
            agent,
            task='f/echo',
            reward=1.0,
            instruction=None,
            version='1.0.0',
            workspace={'greeting.txt': 'hello\n'},
            family=family,
        )
        for job, trial in (('job-2', 'echo__first'), ('job-3', 'echo__second')):
            record_trial(
                data_dir / 'jobs' / 'f' / job,
                trial,
                agent,
                task='f/echo',
                reward=1.0,
                instruction=None,
                version='1.1.0',
                workspace={'greeting.txt': 'hello\n'},
                source=origin,
                family=family,
            )
        break_setting(case_task(family, 'echo'), ('task', 'version'), '1.1.0')
        diff = Mock(wraps=workspace_diff)
        monkeypatch.setattr(jobs, 'workspace_diff', diff)

        outputs = jobs.collect(family, {'acme-large': make_system()})

        assert [(output.job, output.trial) for output in outputs] == [
            ('job-1', 'echo__origin')
        ]
        assert (outputs[0].text, outputs[0].reward, outputs[0].gate_failures) == (
            GREETING_DIFF,
            1.0,
            [],
        )
        diff.assert_called_once_with(
            case_task(family, 'echo') / 'environment' / 'app',
            data_dir / 'jobs' / 'f' / 'job-1' / 'echo__origin' / 'artifacts' / 'app',
        )

    def test_prefers_the_current_request_of_a_task_family_under_its_harness(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        (run,) = jobs.family_systems(family, [system])
        config = jobs.job(family, [run], [case_task(family, 'echo')])
        agent = config.agents[0]
        current = agent.kwargs
        for job, kwargs in (
            ('job-1', {**current, 'max_turns': 99}),
            ('job-2', current),
        ):
            finish_job(
                config.model_copy(
                    update={
                        'job_name': job,
                        'agents': [agent.model_copy(update={'kwargs': kwargs})],
                    }
                )
            )

        (output,) = jobs.collect(family, {system.name: system})

        assert output.job == 'job-2'

    def test_excludes_a_system_the_family_no_longer_lists(self, data_dir: Path) -> None:
        system = make_system()
        family = votes_family(systems=['other'], reference='other', samples=1)
        tasks = render_cases(
            family,
            [Case(id='c', category='reply', source='held-out', instruction='Hi.')],
        )
        for job, bound in (('job-1', 100), ('job-2', 300)):
            config = jobs.job(
                family, [system.model_copy(update={'max_output_tokens': bound})], tasks
            )
            finish_generation(
                config.model_copy(update={'job_name': job}), f'reply at {bound}'
            )

        outputs = jobs.collect(family, {system.name: system})

        assert outputs == []

    @pytest.mark.parametrize(
        ('metadata', 'timing', 'latency'),
        [
            pytest.param(
                {'latency_sec': 1.5},
                TimingInfo(started_at=START, finished_at=START + timedelta(seconds=9)),
                1.5,
                id='recorded-by-the-agent',
            ),
            pytest.param(
                {},
                TimingInfo(
                    started_at=START, finished_at=START + timedelta(seconds=12.25)
                ),
                12.25,
                id='agent-execution',
            ),
            pytest.param({}, TimingInfo(started_at=START), None, id='unfinished'),
            pytest.param({}, None, None, id='no-timing'),
        ],
    )
    def test_takes_the_latency_from_the_agent_or_its_execution_time(
        self,
        data_dir: Path,
        metadata: dict[str, Any],
        timing: TimingInfo | None,
        latency: float | None,
    ) -> None:
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(VOTES),
            task='f/a',
            context=AgentContext(metadata=metadata),
            agent_execution=timing,
            family=VOTES,
        )

        (output,) = jobs.collect(VOTES, {'acme-large': make_system()})

        assert output.latency_sec == latency

    def test_names_an_unknown_harness(self, families_dir: Path) -> None:
        family = with_settings(voted_tasks(families_dir), harness='claude-code')
        system = make_system()

        with pytest.raises(ValueError) as error:
            jobs.collect(family, {system.name: system})

        assert str(error.value) == (
            'Family `f`: harness `claude-code` is not one of `generate`, `terminus-2`.'
        )

    @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
    def test_leaves_a_current_system_without_a_price_unpriced(
        self, data_dir: Path, api: str
    ) -> None:
        system = make_system(price=None, api=api)
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(VOTES, system),
            task='f/a',
            context=AgentContext(
                n_input_tokens=10,
                n_cache_tokens=8,
                n_output_tokens=5,
                cost_usd=0.0,
            ),
            family=VOTES,
        )

        (output,) = jobs.collect(VOTES, {system.name: system})

        assert output.system == system.name
        assert output.cost_usd is None

    def test_names_a_trial_whose_result_is_truncated(self, data_dir: Path) -> None:
        trial = data_dir / 'jobs' / 'f' / 'job-1' / 'a__1'
        record_trial(
            trial.parent, trial.name, current_agent(VOTES), task='f/a', family=VOTES
        )
        result = trial / 'result.json'
        result.write_text(result.read_text()[:20])

        with pytest.raises(ValueError) as error:
            jobs.collect(VOTES, {'acme-large': make_system()})

        assert str(error.value).startswith(
            f"Source trial '{trial.resolve()}' has an unreadable result.json"
        )


class TestReadTrials:
    @pytest.fixture(autouse=True)
    def current_sources(self, families_dir: Path) -> None:
        write_case_family(families_dir, ['acme-large'], name='f')

    def test_skips_a_removed_case_without_reading_its_installed_instruction(
        self, data_dir: Path
    ) -> None:
        case = Case(id='a', category='reply', source='synthetic', instruction='Hi.')
        (task,) = render_cases(VOTES, [case])
        job = data_dir / 'jobs' / 'f' / 'job-1'
        write_trial(
            job,
            'a__1',
            current_agent(),
            task='f/a',
            output='An old reply.',
            instruction=None,
        )
        declare_cases(VOTES, [])
        (task / 'instruction.md').unlink()

        outputs = jobs.read_trials(VOTES, job, {'acme-large': make_system()})

        assert outputs == []

    def test_returns_one_output_per_finished_trial(self, data_dir: Path) -> None:
        declare_cases(
            VOTES,
            [Case(id='a', category='reply', source='synthetic', instruction='Hi.')],
        )
        job = data_dir / 'jobs' / 'f' / 'job-1'
        for attempt in range(3):
            write_trial(
                job,
                f'a__{attempt}',
                trial_config.AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
                task='f/a',
                output=f'Reply {attempt}.',
            )

        outputs = jobs.read_trials(VOTES, job, {})

        assert sorted(
            (output.case, output.system, output.text) for output in outputs
        ) == [('a', 's', f'Reply {attempt}.') for attempt in range(3)]
