import asyncio
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx
import pytest
from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.task import config as task_config
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult
from openai import AsyncOpenAI

from assistant_bench import jobs
from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import load_cases, load_family
from assistant_bench.harnesses.host import HostEnvironment
from support import StubEndpoint, make_system, response


pytestmark = pytest.mark.usefixtures('export')


OPERATIONS: dict[
    str, Callable[[HostEnvironment, Path], Coroutine[Any, Any, object]]
] = {
    'exec': lambda env, path: env.exec('true'),
    'upload-file': lambda env, path: env.upload_file(path, '/a'),
    'upload-dir': lambda env, path: env.upload_dir(path, '/a'),
    'download-file': lambda env, path: env.download_file('/a', path),
    'download-dir': lambda env, path: env.download_dir('/a', path),
}


async def run_in_process(config: JobConfig) -> None:
    await (await Job.create(config)).run()


class TestHostEnvironment:
    @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
    def test_records_the_harbor_deadline_after_closing_the_request_without_retrying(
        self,
        endpoint: StubEndpoint,
        endpoint_clients: list[AsyncOpenAI],
        export: NixosConfigExport,
        model_requests: list[httpx.Request],
        monkeypatch: pytest.MonkeyPatch,
        api: str,
    ) -> None:
        for name, value in endpoint.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(jobs, 'AGENT_TIMEOUT_SEC', 1.0)
        family = load_family('smoke-text')
        system = make_system(api=api)
        gate = endpoint.response_gate = threading.Event()
        config = jobs.job(
            family,
            [system],
            jobs.render_tasks(family, load_cases(family)[:1]),
        ).model_copy(update={'job_name': 'deadline'})

        async def expire() -> None:
            task = asyncio.create_task(run_in_process(config))
            try:
                assert await asyncio.to_thread(endpoint.request_received.wait, 5)
                await asyncio.wait_for(task, 10)
            finally:
                gate.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(expire())

        (result_file,) = (config.jobs_dir / config.job_name).glob('*/result.json')
        result = TrialResult.model_validate_json(result_file.read_text())
        assert result.exception_info is not None
        assert result.exception_info.exception_type == 'AgentTimeoutError'
        assert result.finished_at is not None
        assert result.agent_execution is not None
        assert result.agent_execution.finished_at is not None
        assert result.agent_result is not None
        context = result.agent_result
        assert (
            context.n_input_tokens,
            context.n_cache_tokens,
            context.n_output_tokens,
            context.cost_usd,
        ) == (None, None, None, None)
        assert context.metadata is not None
        assert context.metadata['nixos_config_rev'] == export.rev
        assert context.metadata['latency_sec'] > 0
        assert not (result_file.parent / 'agent' / 'output.txt').exists()
        (client,) = endpoint_clients
        assert client.max_retries == 0
        assert client.is_closed()
        (request,) = endpoint.requests
        assert request.path == (
            '/v1/responses' if api == 'openai-responses' else '/v1/chat/completions'
        )
        (sent,) = model_requests
        assert sent.extensions['timeout'] == {
            'connect': 5.0,
            'read': None,
            'write': 600.0,
            'pool': 600.0,
        }

    @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
    def test_runs_generate_on_each_case_once_across_reruns(
        self,
        data_dir: Path,
        endpoint: StubEndpoint,
        monkeypatch: pytest.MonkeyPatch,
        api: str,
    ) -> None:
        for name, value in endpoint.env.items():
            monkeypatch.setenv(name, value)
        family = load_family('smoke-text')
        cases = load_cases(family)
        system = make_system(api=api)
        if api == 'openai-responses':
            endpoint.response = response()
        family = family.model_copy(
            update={
                'settings': family.settings.model_copy(
                    update={'systems': [system.name], 'reference': system.name}
                )
            }
        )

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
        assert all(output.cost_usd == pytest.approx(0.00046) for output in outputs)
        prompts = [
            request.body['input']
            if api == 'openai-responses'
            else request.body['messages'][0]['content']
            for request in endpoint.requests
        ]
        assert sorted(prompts) == sorted(case.instruction for case in cases)

    @pytest.mark.parametrize(
        'operation',
        OPERATIONS,
    )
    def test_refuses_to_run_a_command_or_move_a_file(
        self,
        tmp_path: Path,
        operation: str,
    ) -> None:
        environment = HostEnvironment(
            environment_dir=tmp_path / 'environment',
            environment_name='a',
            session_id='a__env',
            trial_paths=TrialPaths(tmp_path / 'trial'),
            task_env_config=task_config.EnvironmentConfig(),
        )

        with pytest.raises(RuntimeError, match='starts no container'):
            asyncio.run(OPERATIONS[operation](environment, tmp_path / 'file'))
