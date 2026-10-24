import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.task import config as task_config
from harbor.models.trial.paths import TrialPaths

from assistant_bench import jobs
from assistant_bench.families import load_cases, load_family
from assistant_bench.harnesses.host import HostEnvironment
from support import StubEndpoint, make_system


pytestmark = pytest.mark.usefixtures('export')


async def run_in_process(config: JobConfig) -> None:
    await (await Job.create(config)).run()


class TestHostEnvironment:
    def test_runs_generate_on_each_case_once_across_reruns(
        self, data_dir: Path, endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in endpoint.env.items():
            monkeypatch.setenv(name, value)
        family = load_family('smoke-text')
        cases = load_cases(family)
        system = make_system()

        # Harbor names a job after the second it starts, so the two jobs get names
        # of their own. The first runs in-process for coverage, and the rerun goes
        # through the Harbor CLI, which `bench run` uses.
        first = jobs.job(family, [system], jobs.render_tasks(family, cases))
        asyncio.run(run_in_process(first.model_copy(update={'job_name': 'job-1'})))
        rerun = jobs.job(family, [system], jobs.render_tasks(family, cases))
        jobs.run_job(rerun.model_copy(update={'job_name': 'job-2'}))

        logs = [
            path.read_text()
            for path in (data_dir / 'jobs' / 'smoke-text').glob('*/*/trial.log')
        ]
        assert logs
        assert not any('starts no container' in log for log in logs)
        outputs = jobs.collect(family, {system.name: system})
        assert [
            (output.case, output.error, output.text, output.output_tokens)
            for output in outputs
        ] == [
            ('en-greeting', None, 'Hello there.', 40),
            ('zh-greeting', None, 'Hello there.', 40),
        ]
        prompts = [
            request.body['messages'][0]['content'] for request in endpoint.requests
        ]
        assert sorted(prompts) == sorted(case.instruction for case in cases)

    @pytest.mark.parametrize(
        'call',
        [
            pytest.param(lambda env, path: env.exec('true'), id='exec'),
            pytest.param(
                lambda env, path: env.upload_file(path, '/a'), id='upload-file'
            ),
            pytest.param(lambda env, path: env.upload_dir(path, '/a'), id='upload-dir'),
            pytest.param(
                lambda env, path: env.download_file('/a', path), id='download-file'
            ),
            pytest.param(
                lambda env, path: env.download_dir('/a', path), id='download-dir'
            ),
        ],
    )
    def test_refuses_to_run_a_command_or_move_a_file(
        self,
        tmp_path: Path,
        call: Callable[[HostEnvironment, Path], Coroutine[Any, Any, object]],
    ) -> None:
        environment = HostEnvironment(
            environment_dir=tmp_path / 'environment',
            environment_name='a',
            session_id='a__env',
            trial_paths=TrialPaths(tmp_path / 'trial'),
            task_env_config=task_config.EnvironmentConfig(),
        )

        with pytest.raises(RuntimeError, match='starts no container'):
            asyncio.run(call(environment, tmp_path / 'file'))
