import subprocess
from pathlib import Path
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.task.task import Task
from harbor.models.trial.config import AgentConfig
from harbor.publisher.packager import Packager

from assistant_bench import jobs
from assistant_bench.families import (
    Family,
    Gates,
    TaskTestsSettings,
    VotesSettings,
    load_cases,
    load_family,
)
from assistant_bench.paths import REPO_ROOT
from assistant_bench.scoring.gates import Gate
from support import make_system, write_trial


VOTES = Family(
    name='f',
    source='cases',
    settings=VotesSettings(scorer='votes', samples=2, max_output_tokens=300),
)
TESTS = Family(
    name='g', source='tasks', settings=TaskTestsSettings(scorer='tests', samples=1)
)


class TestRenderTasks:
    def test_writes_a_versioned_task_on_the_stock_image(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        (case, _) = load_cases(family)

        (path,) = jobs.render_tasks(family, [case])

        assert path == data_dir / 'tasks' / 'smoke-text' / 'en-greeting'
        task = Task(path, disable_verification=True)
        assert task.config.task is not None
        assert (task.config.task.name, task.config.task.version) == (
            'smoke-text/en-greeting',
            jobs.TASK_VERSION,
        )
        assert task.config.environment.docker_image == jobs.STOCK_IMAGE
        assert task.instruction == case.instruction


class TestChangedTasks:
    @staticmethod
    def record(data_dir: Path, task: Path, version: str = jobs.TASK_VERSION) -> None:
        content_hash, _ = Packager.compute_content_hash(task)
        write_trial(
            data_dir / 'jobs' / 'smoke-text' / 'job-1',
            f'{task.name}__1',
            AgentConfig(name='oracle'),
            task=f'smoke-text/{task.name}',
            instruction=None,
            digest=f'sha256:{content_hash}',
            version=version,
        )

    def test_names_a_case_edited_after_a_trial_ran_it(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        cases = load_cases(family)
        for task in jobs.render_tasks(family, cases):
            self.record(data_dir, task)
        edited = [
            cases[0].model_copy(update={'instruction': 'Reworded.'}),
            cases[1],
            cases[1].model_copy(update={'id': 'zh-greeting-2', 'instruction': 'New.'}),
        ]

        changed = jobs.changed_tasks(family, jobs.render_tasks(family, edited))

        assert changed == ['en-greeting']

    def test_names_every_task_when_the_stock_image_changes(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        family = load_family('smoke-text')
        cases = load_cases(family)
        for task in jobs.render_tasks(family, cases):
            self.record(data_dir, task)
        monkeypatch.setattr(jobs, 'STOCK_IMAGE', 'debian:forky-slim')

        changed = jobs.changed_tasks(family, jobs.render_tasks(family, cases))

        assert changed == ['en-greeting', 'zh-greeting']

    def test_ignores_trials_of_another_task_version(self, data_dir: Path) -> None:
        family = load_family('smoke-text')
        (case, _) = load_cases(family)
        (task,) = jobs.render_tasks(family, [case])
        self.record(data_dir, task, version='0.9.0')
        edited = case.model_copy(update={'instruction': 'Reworded.'})

        changed = jobs.changed_tasks(family, jobs.render_tasks(family, [edited]))

        assert changed == []


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
        assert [jobs.Output.model_validate_json(line) for line in written] == outputs

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
            settings=VotesSettings(scorer='votes', samples=1, max_output_tokens=300),
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

    def test_keeps_a_renamed_system_beside_its_old_name(self, data_dir: Path) -> None:
        system = make_system()
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(scorer='votes', samples=1, max_output_tokens=300),
        )
        jobs_dir = data_dir / 'jobs' / 'f'
        for job, name in (('job-1', 'old-name'), ('job-2', 'new-name')):
            agent = AgentConfig(
                import_path='assistant_bench.harnesses.generate:Generate',
                kwargs={'system': name, 'fingerprint': system.fingerprint},
            )
            write_trial(jobs_dir / job, 'a__1', agent, task='f/a', output=name)

        outputs = jobs.ingest(family, {})

        assert [(o.system, o.text) for o in outputs] == [
            ('new-name', 'new-name'),
            ('old-name', 'old-name'),
        ]

    def test_gates_each_output_against_its_case_instruction(
        self, data_dir: Path
    ) -> None:
        family = Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                samples=1,
                max_output_tokens=300,
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

    def test_reads_the_reward_only_for_a_tests_scorer(self, data_dir: Path) -> None:
        oracle = AgentConfig(name='oracle')
        for family in ('f', 'g'):
            job_dir = data_dir / 'jobs' / family / 'job-1'
            write_trial(job_dir, 'a__1', oracle, task=f'{family}/a', reward=1.0)

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
