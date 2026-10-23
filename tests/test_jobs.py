import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, SourceJobConfig
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
    load_cases,
    load_family,
)
from assistant_bench.harnesses.generate import GenerateOptions
from assistant_bench.outputs import Output
from assistant_bench.paths import REPO_ROOT
from support import finish_job, make_system, write_trial


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


def request(system: System, max_output_tokens: int = 300) -> dict[str, Any]:
    """Return the agent kwargs that a family with this bound sends a system."""
    return GenerateOptions(
        system=system.name,
        fingerprint=system.fingerprint,
        effort=system.effort,
        system_prompt=system.prompt,
        max_output_tokens=max_output_tokens,
    ).model_dump()


def installed_version(path: Path) -> str:
    config = Task(path, disable_verification=True).config
    assert config.task is not None
    assert config.task.version is not None
    return config.task.version


class TestRenderTasks:
    def test_pins_the_version_of_a_known_task(self) -> None:
        assert (
            jobs.task_version('smoke-text/en-greeting', 'Hi.') == '1.0.0+74bceeb403c7'
        )

    def test_writes_a_versioned_task_on_the_stock_image(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        (case, _) = load_cases(family)

        (path,) = jobs.render_tasks(family, [case])

        assert path == data_dir / 'tasks' / 'smoke-text' / 'en-greeting'
        assert installed_version(path) == jobs.task_version(
            'smoke-text/en-greeting', case.instruction
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

    def test_reruns_every_task_when_the_stock_image_changes(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        family = load_family('smoke-text')
        cases = load_cases(family)
        before = [installed_version(path) for path in jobs.render_tasks(family, cases)]
        monkeypatch.setattr(jobs, 'STOCK_IMAGE', 'debian:forky-slim')

        after = [installed_version(path) for path in jobs.render_tasks(family, cases)]

        assert all(new != old for new, old in zip(after, before, strict=True))

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
    def test_returns_the_recorded_and_current_fingerprints(
        self, data_dir: Path
    ) -> None:
        current = make_system()
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            AgentConfig(
                import_path='x:Y',
                kwargs={'system': current.name, 'fingerprint': 'fp-old'},
            ),
            task='f/a',
        )

        assert jobs.drifted_systems(VOTES, [current]) == [
            (current.name, 'fp-old', current.fingerprint)
        ]

    def test_accepts_a_system_whose_trials_kept_its_fingerprint(
        self, data_dir: Path
    ) -> None:
        current = make_system()
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            AgentConfig(
                import_path='x:Y',
                kwargs={'system': current.name, 'fingerprint': current.fingerprint},
            ),
            task='f/a',
        )

        assert jobs.drifted_systems(VOTES, [current]) == []

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

        assert jobs.drifted_systems(VOTES, [small, large]) == [
            ('acme-large', 'fp-old', large.fingerprint),
            ('acme-small', 'fp-old', small.fingerprint),
        ]

    def test_ignores_a_system_the_run_does_not_select(self, data_dir: Path) -> None:
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            AgentConfig(
                import_path='x:Y',
                kwargs={'system': 'retired', 'fingerprint': 'fp-old'},
            ),
            task='f/a',
        )

        assert jobs.drifted_systems(VOTES, [make_system()]) == []

    def test_ignores_a_trial_that_recorded_no_fingerprint(self, data_dir: Path) -> None:
        current = make_system()
        write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            AgentConfig(import_path='x:Y', kwargs={'system': current.name}),
            task='f/a',
        )

        assert jobs.drifted_systems(VOTES, [current]) == []


class TestCaseJob:
    def test_runs_each_system_through_its_harness_in_its_route_group(
        self, data_dir: Path
    ) -> None:
        large = make_system(prompt='Be brief.')
        small = make_system(name='small', route='acme/small', effort=None)
        task = data_dir / 'tasks' / 'f' / 'c'

        job = jobs.case_job(VOTES, [large, small], [task])

        assert [
            (agent.import_path, agent.model_name, agent.concurrency_group)
            for agent in job.agents
        ] == [
            ('assistant_bench.harnesses.generate:Generate', 'acme/large', 'acme/large'),
            ('assistant_bench.harnesses.generate:Generate', 'acme/small', 'acme/small'),
        ]
        assert job.agents[0].kwargs == {
            'system': 'acme-large',
            'fingerprint': large.fingerprint,
            'effort': 'medium',
            'system_prompt': 'Be brief.',
            'max_output_tokens': 300,
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


class TestOracleJob:
    def test_checks_the_committed_tasks_apart_from_ingested_jobs(
        self, data_dir: Path
    ) -> None:
        job = jobs.oracle_job(load_family('smoke'))

        assert [agent.name for agent in job.agents] == ['oracle']
        assert [t.path for t in job.tasks] == [
            REPO_ROOT / 'families' / 'smoke' / 'tasks' / 'echo'
        ]
        assert not job.verifier.disable
        assert job.source_jobs == []
        assert job.jobs_dir == data_dir / 'checks' / 'smoke'


class TestHasJobs:
    @pytest.mark.parametrize(
        ('directory', 'result', 'expected'),
        [(False, False, False), (True, False, False), (True, True, True)],
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

        assert jobs.has_jobs(family) is expected


class TestRunJob:
    def test_passes_the_config_to_harbor_with_telemetry_off(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[list[Any], dict[str, str], JobConfig]] = []

        def fake_run(args: list[Any], check: bool, env: dict[str, str]) -> None:
            config = JobConfig.model_validate_json(Path(args[3]).read_text())
            calls.append((args, env, config))

        monkeypatch.setattr(subprocess, 'run', fake_run)
        job = jobs.oracle_job(load_family('smoke'))

        job_dir = jobs.run_job(job)

        ((args, env, config),) = calls
        assert Path(args[0]).name == 'harbor'
        assert args[1:3] == ['run', '--config']
        assert args[4:] == ['--yes']
        assert env['HARBOR_TELEMETRY'] == 'off'
        assert config == job
        assert job_dir == data_dir / 'checks' / 'smoke' / job.job_name


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
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family F',
                systems=['acme-large'],
                samples=1,
                max_output_tokens=300,
                reference='acme-large',
                gates=Gates(script='hans'),
            ),
        )
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

        assert [(o.case, o.error) for o in outputs] == [
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
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family F',
                systems=['acme-large'],
                samples=1,
                max_output_tokens=300,
                reference='acme-large',
            ),
        )
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
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family F',
                systems=['acme-large'],
                samples=1,
                max_output_tokens=300,
                reference='acme-large',
            ),
        )
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
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family F',
                systems=['acme-large'],
                samples=1,
                max_output_tokens=300,
                reference='acme-large',
            ),
        )
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
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family F',
                systems=['acme-large'],
                samples=1,
                max_output_tokens=300,
                reference='acme-large',
            ),
        )
        jobs_dir = data_dir / 'jobs' / 'f'
        write_trial(
            jobs_dir / 'job-1', 'a__1', generate, task='f/a', error='TimeoutError'
        )
        write_trial(jobs_dir / 'job-2', 'a__2', generate, task='f/a', output='Hi.')

        outputs = jobs.ingest(family, {system.name: system})

        assert [(o.job, o.case, o.error, o.text) for o in outputs] == [
            ('job-2', 'a', None, 'Hi.')
        ]

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
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family F',
                systems=['acme-large'],
                samples=1,
                max_output_tokens=300,
                reference='acme-large',
            ),
        )
        jobs_dir = data_dir / 'jobs' / 'f'
        samples = list(zip(('job-1', 'job-2'), names, fingerprints, strict=True))
        for job, name, fingerprint in samples:
            agent = AgentConfig(
                import_path='assistant_bench.harnesses.generate:Generate',
                kwargs={'system': name, 'fingerprint': fingerprint},
            )
            write_trial(jobs_dir / job, 'a__1', agent, task='f/a', output=job)

        outputs = jobs.ingest(family, {})

        assert sorted((o.system, o.fingerprint, o.text) for o in outputs) == sorted(
            (name, fingerprint, job) for job, name, fingerprint in samples
        )

    def test_gates_each_output_against_its_case_instruction(
        self, data_dir: Path
    ) -> None:
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family F',
                systems=['acme-large'],
                samples=1,
                max_output_tokens=300,
                reference='acme-large',
                language='zh',
                gates=Gates(script='hans'),
            ),
        )
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

        assert [(o.case, o.gate_failures) for o in outputs] == [
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
