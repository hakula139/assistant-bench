import asyncio
import functools
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.task import config as task_config
from harbor.models.task.task import Task
from harbor.models.trial.config import AgentConfig

from assistant_bench import jobs
from assistant_bench.catalog import System
from assistant_bench.families import (
    Case,
    Family,
    Gate,
    Gates,
    TaskTestsSettings,
    VotesSettings,
    case_task,
    load_cases,
    load_family,
)
from assistant_bench.harnesses import options
from assistant_bench.outputs import Output
from assistant_bench.paths import FAMILIES_DIR, REPO_ROOT
from support import finish_job, make_system, write_task_family, write_trial


VOTES = Family(
    name='f',
    source='cases',
    settings=VotesSettings(
        scorer='votes',
        display_name='Family F',
        systems=['acme-large'],
        samples=2,
        max_output_tokens=300,
        reference='acme-large',
    ),
)
TESTS = Family(
    name='g',
    source='tasks',
    settings=TaskTestsSettings(scorer='tests', display_name='Family G', samples=1),
)


def votes_family(**settings: Any) -> Family:
    """Return `VOTES` with these settings replaced."""
    return VOTES.model_copy(
        update={'settings': VOTES.settings.model_copy(update=settings)}
    )


def request(system: System, max_output_tokens: int = 300) -> dict[str, Any]:
    """Return the agent kwargs that a family with this bound sends a system."""
    settings = VOTES.settings.model_copy(
        update={'max_output_tokens': max_output_tokens}
    )
    return options.generate(system, settings)


def version(config: task_config.TaskConfig) -> str:
    assert config.task is not None
    assert config.task.version is not None
    return config.task.version


def installed_version(path: Path) -> str:
    return version(Task(path, disable_verification=True).config)


class TestRenderTaskConfig:
    def test_pins_the_version_of_a_known_task(self) -> None:
        config = jobs.render_task_config('smoke-text/en-greeting', 'Hi.')

        assert version(config) == '1.0.0+2fe836d90551'

    @pytest.mark.parametrize(
        ('target', 'attribute', 'value'),
        [
            pytest.param(jobs, 'STOCK_IMAGE', 'debian:forky-slim', id='stock-image'),
            pytest.param(jobs, 'AGENT_TIMEOUT_SEC', 60.0, id='agent-timeout'),
            pytest.param(
                task_config,
                'EnvironmentConfig',
                functools.partial(task_config.EnvironmentConfig, cpus=2),
                id='another-environment-setting',
            ),
        ],
    )
    def test_changes_the_version_with_each_setting_it_renders(
        self,
        monkeypatch: pytest.MonkeyPatch,
        target: object,
        attribute: str,
        value: Any,
    ) -> None:
        before = version(jobs.render_task_config('f/a', 'Hi.'))
        monkeypatch.setattr(target, attribute, value)

        after = version(jobs.render_task_config('f/a', 'Hi.'))

        assert after != before


class TestRenderTasks:
    def test_writes_a_versioned_task_on_the_stock_image(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        (case, _) = load_cases(family)

        (path,) = jobs.render_tasks(family, [case])

        assert path == data_dir / 'tasks' / 'smoke-text' / 'en-greeting'
        assert installed_version(path) == version(
            jobs.render_task_config('smoke-text/en-greeting', case.instruction)
        )
        task = Task(path, disable_verification=True)
        assert task.config.environment.docker_image == jobs.STOCK_IMAGE
        assert task.instruction == case.instruction

    def test_reruns_an_edited_case_and_reuses_an_unedited_one(
        self, data_dir: Path
    ) -> None:
        family = load_family('smoke-text')
        cases = load_cases(family)
        first = finish_job(
            jobs.case_job(family, [make_system()], jobs.render_tasks(family, cases))
        )
        edited = [cases[0].model_copy(update={'instruction': 'Reworded.'}), cases[1]]

        job = jobs.case_job(family, [make_system()], jobs.render_tasks(family, edited))
        plan = asyncio.run(JobPlan.from_config(job))
        (source,) = job.source_jobs
        diff = compare_job_trials(plan, load_source_trials([(source, first)]))

        assert {trial.task_name: trial.action for trial in diff.trials} == {
            'smoke-text/en-greeting': DiffAction.RERUN,
            'smoke-text/zh-greeting': DiffAction.REUSE,
        }

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


class TestDriftedSystems:
    CURRENT = make_system()

    @pytest.mark.parametrize(
        ('kwargs', 'expected'),
        [
            pytest.param(
                {'system': CURRENT.name, 'fingerprint': 'fp-old'},
                [(CURRENT.name, 'fp-old', CURRENT.fingerprint)],
                id='changed-fingerprint',
            ),
            pytest.param(
                {'system': CURRENT.name, 'fingerprint': CURRENT.fingerprint},
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
            AgentConfig(import_path='x:Y', kwargs=kwargs),
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
                AgentConfig(
                    import_path='x:Y',
                    kwargs={'system': system.name, 'fingerprint': 'fp-old'},
                ),
                task=f'f/{trial}',
            )

        drifted = jobs.drifted_systems(VOTES, [small, large])

        assert drifted == [
            ('acme-large', 'fp-old', large.fingerprint),
            ('acme-small', 'fp-old', small.fingerprint),
        ]


class TestCaseJob:
    def test_runs_each_system_through_its_harness_in_its_route_group(
        self, data_dir: Path
    ) -> None:
        large = make_system(prompt='Be brief.')
        small = make_system(name='small', route='acme/small', effort=None)
        terminus = make_system(
            name='acme-terminus', route='acme/terminus', harness='terminus-2'
        )
        task = data_dir / 'tasks' / 'f' / 'c'

        job = jobs.case_job(VOTES, [large, small, terminus], [task])

        assert [
            (agent.import_path, agent.model_name, agent.concurrency_group)
            for agent in job.agents
        ] == [
            ('assistant_bench.harnesses.generate:Generate', 'acme/large', 'acme/large'),
            ('assistant_bench.harnesses.generate:Generate', 'acme/small', 'acme/small'),
            (
                'assistant_bench.harnesses.terminus:Terminus',
                'openai/acme/terminus',
                'acme/terminus',
            ),
        ]
        assert job.agents[0].kwargs == {
            'system': 'acme-large',
            'fingerprint': large.fingerprint,
            'effort': 'medium',
            'system_prompt': 'Be brief.',
            'max_output_tokens': 300,
        }
        assert job.agents[2].kwargs == {
            'system': 'acme-terminus',
            'fingerprint': terminus.fingerprint,
            'llm_call_kwargs': {'max_completion_tokens': 300},
            'reasoning_effort': 'medium',
            'record_terminal_session': False,
            'suppress_max_turns_warning': True,
        }
        assert job.n_attempts == 2
        assert job.retry.max_retries == 1
        assert job.verifier.disable
        assert job.jobs_dir == data_dir / 'jobs' / 'f'
        assert [t.path for t in job.tasks] == [task]

    def test_diffs_against_the_earlier_jobs_of_the_family(self, data_dir: Path) -> None:
        jobs_dir = data_dir / 'jobs' / 'f'
        for name in ('job-1', 'job-2', 'job-3'):
            (jobs_dir / name).mkdir(parents=True)
        for name in ('job-1', 'job-3'):
            (jobs_dir / name / 'result.json').write_text('{}')

        job = jobs.case_job(VOTES, [make_system()], [])

        assert job.source_jobs == [
            SourceJobConfig(action='diff', type='local', path=jobs_dir / name)
            for name in ('job-1', 'job-3')
        ]


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
                ('task', 'version'), None, 'set [task] version', id='no-version'
            ),
            pytest.param(
                ('agent', 'network_mode'),
                'public',
                'the agent phase needs no-network',
                id='open-agent-network',
            ),
            pytest.param(
                ('verifier', 'network_mode'),
                'public',
                'the verifier phase needs no-network',
                id='open-verifier-network',
            ),
            pytest.param(
                ('environment', 'network_mode'),
                'allowlist',
                'the agent phase needs no-network',
                id='allowlist-environment',
            ),
            pytest.param(
                ('verifier', 'environment_mode'),
                'shared',
                'set [verifier] environment_mode = "separate"',
                id='explicit-shared-verifier',
            ),
            pytest.param(
                ('verifier', 'environment_mode'),
                None,
                'set [verifier] environment_mode = "separate"',
                id='unset-verifier-mode',
            ),
            pytest.param(
                ('verifier', 'environment'),
                {'cpus': 1},
                'do not set [verifier.environment]',
                id='verifier-environment',
            ),
            pytest.param(
                ('steps',),
                [{'name': 'one'}],
                'do not set [[steps]]',
                id='steps',
            ),
            pytest.param(
                ('artifacts',),
                None,
                'list "/app" under [[artifacts]]',
                id='no-artifact',
            ),
            pytest.param(
                ('artifacts',),
                ['/logs/x'],
                'list "/app" under [[artifacts]]',
                id='artifact-outside-app',
            ),
            pytest.param(
                ('environment', 'cpus'), None, 'set [environment] cpus', id='no-cpus'
            ),
            pytest.param(
                ('environment', 'memory_mb'),
                None,
                'set [environment] memory_mb',
                id='no-memory',
            ),
            pytest.param(
                ('agent', 'timeout_sec'),
                None,
                'set [agent] timeout_sec',
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

        assert f'tasks/echo/task.toml: {message}' in str(error.value)

    def test_accepts_a_string_artifact(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('artifacts',), ['/app'])

        jobs.check_tasks(family, tasks)

    def test_names_a_problem_in_a_later_task(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo', 'again')
        break_setting(tasks[1], ('environment', 'cpus'), None)

        with pytest.raises(ValueError) as error:
            jobs.check_tasks(family, tasks)

        assert 'tasks/again/task.toml: set [environment] cpus' in str(error.value)

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

        assert 'cases/smoke/echo/task.toml: set [environment] cpus' in str(error.value)

    def test_names_every_problem_of_a_task(self, families_dir: Path) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('environment', 'cpus'), None)
        break_setting(tasks[0], ('environment', 'memory_mb'), None)

        with pytest.raises(ValueError) as error:
            jobs.check_tasks(family, tasks)

        assert 'set [environment] cpus' in str(error.value)
        assert 'set [environment] memory_mb' in str(error.value)

    def test_rejects_a_task_toml_that_fails_to_validate(
        self, families_dir: Path
    ) -> None:
        family, tasks = task_family(families_dir, 'echo')
        break_setting(tasks[0], ('environment', 'cpus'), 'two')

        with pytest.raises(ValueError) as error:
            jobs.check_tasks(family, tasks)

        assert str(error.value).startswith(
            "family 'f':\ntasks/echo/task.toml: environment.cpus: Input should be a "
            'valid integer'
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
        assert [t.path for t in job.tasks] == [task]
        assert not job.verifier.disable
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

        It writes its arguments, the variables `run_job` sets, and the config it
        receives beside itself.
        """
        harbor = directory / 'harbor'
        harbor.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\\n' "$@" > "$0.args"
                printf '%s\\n' "$HARBOR_TELEMETRY" "$LITELLM_MODE" > "$0.env"
                cp "$3" "$0.json"
                exit {exit_code}
                """
            )
        )
        harbor.chmod(0o755)
        monkeypatch.setattr(sys, 'executable', str(directory / 'python'))
        return harbor

    def test_runs_harbor_on_the_config_without_telemetry_or_dotenv(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harbor = self.stub_harbor(tmp_path, monkeypatch, 0)
        family = load_family('smoke')
        job = jobs.check_job(family, [case_task(family, 'echo')])

        job_dir = jobs.run_job(job)

        args = Path(f'{harbor}.args').read_text().splitlines()
        assert args[:2] == ['run', '--config']
        assert args[3:] == ['--yes']
        assert Path(f'{harbor}.env').read_text().splitlines() == ['off', 'PRODUCTION']
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

    def test_keeps_the_environment_out_of_a_failing_test_report(
        self, tmp_path: Path
    ) -> None:
        secret = 'sentinel-secret-value'
        test = tmp_path / 'test_failing_harbor.py'
        test.write_text(
            textwrap.dedent(
                """\
                import sys
                from pathlib import Path

                import pytest
                from harbor.models.job.config import JobConfig

                from assistant_bench import jobs


                def test_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
                    harbor = tmp_path / 'harbor'
                    harbor.write_text('#!/bin/sh\\nexit 3\\n')
                    harbor.chmod(0o755)
                    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'python'))
                    jobs.run_job(JobConfig())
                """
            )
        )

        report = subprocess.run(
            [
                *(sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider'),
                *('-c', REPO_ROOT / 'pyproject.toml', '--rootdir', tmp_path),
                *('--no-cov', test),
            ],
            capture_output=True,
            env={
                'BENCH_TEST_SECRET': secret,
                'LITELLM_MODE': 'PRODUCTION',
                'PATH': os.environ['PATH'],
            },
            text=True,
        )
        assert 'CalledProcessError' in report.stdout, report.stdout

        assert secret not in report.stdout + report.stderr


class TestIngest:
    def test_keeps_only_trials_of_the_current_task(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        job = data_dir / 'jobs' / 'smoke-text' / 'job-1'
        agents = {
            side: AgentConfig(
                import_path='x:Y', kwargs={'system': side, 'fingerprint': f'fp-{side}'}
            )
            for side in ('a', 'b')
        }
        write_trial(
            job,
            'en-greeting__a-old',
            agents['a'],
            task='smoke-text/en-greeting',
            output='old',
            version=jobs.TASK_VERSION,
        )
        for case, side in (
            ('en-greeting', 'b'),
            ('zh-greeting', 'a'),
            ('zh-greeting', 'b'),
        ):
            write_trial(
                job,
                f'{case}__{side}',
                agents[side],
                task=f'smoke-text/{case}',
                output=f'{case} {side}',
                context=AgentContext(metadata={'finish_reason': 'stop'}),
            )

        (data_dir / 'tasks' / 'smoke-text' / 'stray.md').write_text('Stray.')

        outputs = jobs.ingest(family, {})

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
        for path in jobs.render_tasks(family, cases):
            write_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{path.name}__1',
                AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
                task=f'f/{path.name}',
                output='你好。',
                instruction=None,
                context=AgentContext(metadata={'finish_reason': 'stop'}),
            )
        jobs.render_tasks(family, cases[:1])

        (output,) = jobs.ingest(family, {})

        assert output.case == 'a'

    def test_keeps_a_trial_whose_instruction_has_a_carriage_return(
        self, data_dir: Path
    ) -> None:
        family = load_family('smoke-text')
        case = Case(
            id='en-greeting',
            category='reply',
            source='held-out',
            instruction='Line one.\r\nLine two.',
        )
        (path,) = jobs.render_tasks(family, [case])
        write_trial(
            data_dir / 'jobs' / 'smoke-text' / 'job-1',
            'en-greeting__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
            task='smoke-text/en-greeting',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
        )

        (output,) = jobs.ingest(family, {})

        assert output.case == path.name

    def test_keeps_trials_when_a_bench_constant_changes(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        family = load_family('smoke-text')
        jobs.render_tasks(family, load_cases(family)[:1])
        write_trial(
            data_dir / 'jobs' / 'smoke-text' / 'job-1',
            'en-greeting__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
            task='smoke-text/en-greeting',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
        )
        monkeypatch.setattr(jobs, 'STOCK_IMAGE', 'debian:forky-slim')

        (output,) = jobs.ingest(family, {})

        assert output.case == 'en-greeting'

    def test_collects_generations_and_errors(self, data_dir: Path) -> None:
        job_dir = data_dir / 'jobs' / 'f' / 'job-1'
        system = make_system()
        generate = AgentConfig(
            import_path='assistant_bench.harnesses.generate:Generate',
            kwargs={'system': system.name, 'fingerprint': system.fingerprint},
        )
        write_trial(
            job_dir,
            'b__1',
            generate,
            task='f/b',
            output='Hi.',
            context=AgentContext(
                n_input_tokens=1_000,
                n_cache_tokens=600,
                n_output_tokens=500,
                metadata={
                    'reasoning_tokens': 100,
                    'latency_sec': 1.5,
                    'finish_reason': 'stop',
                    'response_model': 'large-2026',
                    'request_id': 'req-1',
                    'nixos_config_rev': 'rev-a',
                },
            ),
        )
        write_trial(job_dir, 'a__1', generate, task='f/a', error='TimeoutError')

        outputs = jobs.ingest(VOTES, {system.name: system})

        assert [(output.case, output.error) for output in outputs] == [
            ('a', 'TimeoutError'),
            ('b', None),
        ]
        assert (outputs[0].cost_usd, outputs[0].gate_failures) == (None, None)
        assert outputs[1].model_dump(exclude={'case', 'error'}) == {
            'trial': 'b__1',
            'job': 'job-1',
            'system': 'acme-large',
            'fingerprint': system.fingerprint,
            'nixos_config_rev': 'rev-a',
            'text': 'Hi.',
            'reward': None,
            'gate_failures': [],
            'finish_reason': 'stop',
            'input_tokens': 1_000,
            'cached_tokens': 600,
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
        system = make_system()
        jobs_dir = data_dir / 'jobs' / 'f'
        for job, tokens, output in (
            ('2026-10-20__10-00-00', 600, 'old request'),
            ('2026-10-21__10-00-00', 300, 'current request'),
        ):
            write_trial(
                jobs_dir / job,
                'a__1',
                AgentConfig(import_path='x:Y', kwargs=request(system, tokens)),
                task='f/a',
                output=output,
                context=AgentContext(
                    n_output_tokens=tokens, metadata={'finish_reason': 'stop'}
                ),
            )

        outputs = jobs.ingest(family, {system.name: system})

        assert [
            (output.job, output.text, output.output_tokens) for output in outputs
        ] == [('2026-10-21__10-00-00', 'current request', 300)]

    def test_keeps_the_oldest_trial_that_sent_the_current_request(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        jobs_dir = data_dir / 'jobs' / 'f'
        for job, output in (
            ('2026-10-20__10-00-00', 'first'),
            ('2026-10-21__10-00-00', 'second'),
        ):
            write_trial(
                jobs_dir / job,
                'a__1',
                AgentConfig(import_path='x:Y', kwargs=request(system)),
                task='f/a',
                output=output,
                context=AgentContext(metadata={'finish_reason': 'stop'}),
            )

        outputs = jobs.ingest(family, {system.name: system})

        assert [(output.job, output.text) for output in outputs] == [
            ('2026-10-20__10-00-00', 'first')
        ]

    def test_keeps_an_older_success_over_a_newer_error(self, data_dir: Path) -> None:
        family = votes_family(samples=1)
        system = make_system()
        jobs_dir = data_dir / 'jobs' / 'f'
        write_trial(
            jobs_dir / '2026-10-20__10-00-00',
            'a__1',
            AgentConfig(import_path='x:Y', kwargs=request(system)),
            task='f/a',
            output='kept',
            context=AgentContext(metadata={'finish_reason': 'stop'}),
        )
        write_trial(
            jobs_dir / '2026-10-21__10-00-00',
            'a__2',
            AgentConfig(import_path='x:Y', kwargs=request(system)),
            task='f/a',
            error='TimeoutError',
        )

        outputs = jobs.ingest(family, {system.name: system})

        assert [(output.job, output.error, output.text) for output in outputs] == [
            ('2026-10-20__10-00-00', None, 'kept')
        ]

    def test_keeps_a_rerun_success_over_the_error_it_replaced(
        self, data_dir: Path
    ) -> None:
        system = make_system()
        generate = AgentConfig(
            import_path='assistant_bench.harnesses.generate:Generate',
            kwargs={'system': system.name, 'fingerprint': system.fingerprint},
        )
        family = votes_family(samples=1)
        jobs_dir = data_dir / 'jobs' / 'f'
        write_trial(
            jobs_dir / 'job-1', 'a__1', generate, task='f/a', error='TimeoutError'
        )
        write_trial(jobs_dir / 'job-2', 'a__2', generate, task='f/a', output='Hi.')

        outputs = jobs.ingest(family, {system.name: system})

        assert [
            (output.job, output.case, output.error, output.text) for output in outputs
        ] == [('job-2', 'a', None, 'Hi.')]

    @pytest.mark.parametrize(
        ('names', 'fingerprints'),
        [
            (('old-name', 'new-name'), ('fp', 'fp')),
            (('acme-large', 'acme-large'), ('fp-old', 'fp-new')),
        ],
        ids=['renamed', 'new-fingerprint'],
    )
    def test_keeps_samples_per_name_and_fingerprint(
        self, data_dir: Path, names: tuple[str, str], fingerprints: tuple[str, str]
    ) -> None:
        family = votes_family(samples=1)
        jobs_dir = data_dir / 'jobs' / 'f'
        samples = list(zip(('job-1', 'job-2'), names, fingerprints, strict=True))
        for job, name, fingerprint in samples:
            agent = AgentConfig(
                import_path='assistant_bench.harnesses.generate:Generate',
                kwargs={'system': name, 'fingerprint': fingerprint},
            )
            write_trial(jobs_dir / job, 'a__1', agent, task='f/a', output=job)

        outputs = jobs.ingest(family, {})

        assert sorted(
            (output.system, output.fingerprint, output.text) for output in outputs
        ) == sorted((name, fingerprint, job) for job, name, fingerprint in samples)

    def test_gates_each_output_against_its_case_instruction(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1, language='zh', gates=Gates(script='hans'))
        generate = AgentConfig(import_path='x:Y', kwargs={'system': 's'})
        stop = AgentContext(metadata={'finish_reason': 'stop'})
        for case, output in (('a', '臺北今天很热。'), ('b', '臺北今天很熱。')):
            write_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{case}__1',
                generate,
                task=f'f/{case}',
                output=output,
                instruction='介绍一下臺北的天气。',
                context=stop,
            )

        outputs = jobs.ingest(family, {})

        assert [(output.case, output.gate_failures) for output in outputs] == [
            ('a', []),
            ('b', [Gate.SCRIPT]),
        ]

    def test_reads_no_instruction_without_the_script_gate(self, data_dir: Path) -> None:
        task = data_dir / 'tasks' / 'f' / 'a'
        task.mkdir(parents=True)
        (task / 'task.toml').write_text(
            jobs.render_task_config('f/a', 'Hi.').model_dump_toml()
        )
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
            task='f/a',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
        )

        (output,) = jobs.ingest(VOTES, {})

        assert output.gate_failures == []

    def test_reads_the_reward_only_for_a_tests_scorer(self, data_dir: Path) -> None:
        oracle = AgentConfig(name='oracle')
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1', 'a__1', oracle, task='f/a', reward=1.0
        )
        write_trial(
            data_dir / 'jobs' / 'g' / 'job-1',
            'a__1',
            oracle,
            task='g/a',
            reward=1.0,
            instruction=None,
            version=jobs.TASK_VERSION,
        )

        (scored,) = jobs.ingest(TESTS, {})
        (voted,) = jobs.ingest(VOTES, {})

        assert (scored.case, scored.system, scored.reward) == ('a', 'oracle', 1.0)
        assert scored.gate_failures is None
        assert voted.reward is None

    def test_leaves_a_system_missing_from_the_catalog_unpriced(
        self, data_dir: Path
    ) -> None:
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 'retired'}),
            task='f/a',
            context=AgentContext(n_input_tokens=10, n_output_tokens=5),
        )

        (output,) = jobs.ingest(VOTES, {})

        assert output.system == 'retired'
        assert output.cost_usd is None

    def test_names_a_trial_whose_result_is_truncated(self, data_dir: Path) -> None:
        trial = data_dir / 'jobs' / 'f' / 'job-1' / 'a__1'
        write_trial(
            trial.parent,
            trial.name,
            AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
            task='f/a',
        )
        result = trial / 'result.json'
        result.write_text(result.read_text()[:20])

        with pytest.raises(ValueError) as error:
            jobs.ingest(VOTES, {})

        assert str(error.value).startswith(
            f"Source trial '{trial.resolve()}' has an unreadable result.json"
        )


class TestReadTrials:
    def test_returns_one_output_per_finished_trial(self, data_dir: Path) -> None:
        job = data_dir / 'jobs' / 'f' / 'job-1'
        write_trial(
            job,
            'a__1',
            AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
            task='f/a',
            output='Hi.',
        )

        (output,) = jobs.read_trials(VOTES, job, {})

        assert (output.case, output.system, output.text) == ('a', 's', 'Hi.')
