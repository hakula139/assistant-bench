import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from harbor.agents.installed.claude_code import ClaudeCode as HarborClaudeCode
from harbor.agents.installed.codex import Codex as HarborCodex
from harbor.agents.options import InstalledAgentOptions, cli_annotation, env_annotation
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from assistant_bench import jobs
from assistant_bench.harnesses import agent_kwargs, fingerprint
from assistant_bench.harnesses.installed import ClaudeCode, Codex
from assistant_bench.state import State
from support import (
    VOTES,
    StubEndpoint,
    accounted_trial,
    make_system,
    message,
    message_stream,
    response,
    voted_tasks,
)


pytestmark = pytest.mark.usefixtures('export')


class TestInstalled:
    @pytest.mark.parametrize(
        'agent_type', [ClaudeCode, Codex], ids=['claude-code', 'codex']
    )
    def test_refuses_nonlocal_context_before_installation(
        self,
        logs_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        agent_type: type[ClaudeCode] | type[Codex],
    ) -> None:
        claude = agent_type is ClaudeCode
        system = make_system(
            api='anthropic-messages' if claude else 'openai-responses',
            thinking_mode='anthropic-adaptive' if claude else None,
            harness='claude-code' if claude else 'codex',
            version='1.2.3',
        )
        agent = agent_type(logs_dir=logs_dir, **agent_kwargs(system))
        environment = AsyncMock(spec=BaseEnvironment)
        download = Mock()
        monkeypatch.setattr(
            'assistant_bench.harnesses.installed.check_local_context',
            AsyncMock(side_effect=ValueError('context refused')),
        )
        monkeypatch.setattr(
            'assistant_bench.harnesses.installed.cache.artifact', download
        )

        with pytest.raises(ValueError, match='context refused'):
            asyncio.run(agent.install(environment))

        download.assert_not_called()
        environment.exec.assert_not_awaited()
        environment.upload_file.assert_not_awaited()

    @pytest.mark.parametrize(
        ('agent_type', 'parent_type', 'api', 'harness', 'prefix'),
        [
            (
                ClaudeCode,
                HarborClaudeCode,
                'anthropic-messages',
                'claude-code',
                'anthropic/',
            ),
            (Codex, HarborCodex, 'openai-responses', 'codex', 'openai/'),
        ],
        ids=['claude-code', 'codex'],
    )
    @pytest.mark.parametrize('cancelled', [False, True], ids=['finished', 'cancelled'])
    def test_records_each_completed_response_and_keeps_endpoint_credentials_on_the_host(
        self,
        private_state: State,
        endpoint: StubEndpoint,
        logs_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        agent_type: type[ClaudeCode] | type[Codex],
        parent_type: type[HarborClaudeCode] | type[HarborCodex],
        api: str,
        harness: str,
        prefix: str,
        cancelled: bool,
    ) -> None:
        system = make_system(
            api=api,
            harness=harness,
            version='1.2.3',
            thinking_mode='anthropic-adaptive' if harness == 'claude-code' else None,
        )
        payload = message() if harness == 'claude-code' else response()
        event = {'type': 'response.completed', 'response': payload}
        stream = (
            message_stream(payload)
            if harness == 'claude-code'
            else [f'data: {json.dumps(event)}\n\n'.encode()]
        )
        endpoint.streams = [stream, stream]
        agent = agent_type(
            logs_dir=logs_dir,
            model_name=prefix + system.route,
            extra_env=endpoint.env,
            **agent_kwargs(system),
        )
        agent.binary_sha256 = 'binary-digest'
        environment = AsyncMock(spec=BaseEnvironment)
        if cancelled:
            environment.download_dir.side_effect = OSError('The container stopped.')
        context = AgentContext()
        received_keys: list[str | None] = []

        async def run(
            self: HarborClaudeCode | HarborCodex,
            instruction: str,
            environment: BaseEnvironment,
            context: AgentContext,
        ) -> None:
            access = self.model_connection
            received_keys.append(access.api_key)
            assert access.execution_base_url is not None
            url = access.execution_base_url.replace('host.docker.internal', '127.0.0.1')
            suffix = '/v1/messages' if harness == 'claude-code' else '/responses'
            async with httpx2.AsyncClient() as client:
                for _ in range(2):
                    reply = await client.post(
                        url + suffix,
                        json={'model': access.model_id, 'stream': True},
                        headers={'Authorization': f'Bearer {access.api_key}'},
                    )
                    assert reply.status_code == 200
            if cancelled:
                raise asyncio.CancelledError

        def populate(
            self: HarborClaudeCode | HarborCodex, context: AgentContext
        ) -> None:
            context.n_input_tokens = 999
            context.cost_usd = 999

        monkeypatch.setattr(parent_type, 'run', run)
        monkeypatch.setattr(parent_type, 'populate_context_post_run', populate)

        with accounted_trial(
            private_state,
            system=system.name,
            fingerprint=fingerprint(system),
            trial=logs_dir.parent.name,
        ) as recorder:
            if cancelled:
                with pytest.raises(asyncio.CancelledError):
                    asyncio.run(agent.run('Write the greeting.', environment, context))
            else:
                asyncio.run(agent.run('Write the greeting.', environment, context))

        assert received_keys[0] and received_keys[0] != 'test-key'
        assert context.n_input_tokens == (260 if harness == 'claude-code' else 240)
        assert context.n_output_tokens == 80
        assert context.metadata is not None
        assert context.metadata['cache_write_tokens'] == (
            20 if harness == 'claude-code' else 0
        )
        assert context.metadata['binary_sha256'] == 'binary-digest'
        attempt = recorder.attempt()
        assert attempt is not None
        assert attempt.system == system.name
        assert attempt.fingerprint == fingerprint(system)
        assert all(usage and usage.output_tokens == 40 for usage in attempt.responses)
        assert len(attempt.responses) == 2
        environment.download_dir.assert_awaited_once()
        assert all(
            request.headers['authorization'] == 'Bearer test-key'
            for request in endpoint.requests
        )

    @pytest.mark.parametrize(
        'agent_type', [ClaudeCode, Codex], ids=['claude-code', 'codex']
    )
    def test_options_explicitly_disable_every_environment_fallback(
        self, agent_type: type[ClaudeCode] | type[Codex]
    ) -> None:
        claude = agent_type is ClaudeCode
        system = make_system(
            api='anthropic-messages' if claude else 'openai-responses',
            thinking_mode='anthropic-adaptive' if claude else None,
            harness='claude-code' if claude else 'codex',
            version='1.2.3',
        )
        configured = agent_kwargs(system)
        fallbacks = {
            name
            for name, field in agent_type.options_model.model_fields.items()
            if any(
                item is not None and item.fallback is not None
                for item in (cli_annotation(field), env_annotation(field))
            )
        }

        options = agent_type.parse_options(configured)

        assert isinstance(options, InstalledAgentOptions)
        assert options.disable_web_search
        assert fallbacks <= configured.keys()
        assert options.version == '1.2.3'


class TestJobConfiguration:
    def test_requires_a_task_environment_for_an_installed_agent(self) -> None:
        system = make_system(harness='codex', version='1.2.3', api='openai-responses')

        with pytest.raises(ValueError, match='requires a task environment'):
            jobs.check_systems(VOTES, [system])

    def test_lists_only_the_trial_proxy_in_a_mixed_harness_job(
        self, families_dir: Path, tmp_path: Path
    ) -> None:
        family = voted_tasks(families_dir)
        system = make_system(harness='codex', version='1.2.3', api='openai-responses')
        ordinary = make_system(name='other')

        selected = jobs.family_systems(family, [system, ordinary])
        config = jobs.job(family, selected, [], root=tmp_path, sources=[])

        assert [agent.import_path for agent in config.agents] == [
            'assistant_bench.harnesses.installed:Codex',
            'assistant_bench.harnesses.terminus:Terminus',
        ]
        assert config.agents[0].model_name == 'openai/acme/large'
        assert (
            config.environment.import_path
            == 'assistant_bench.harnesses.docker:InstalledDocker'
        )
        assert config.agents[0].extra_allowed_hosts == ['host.docker.internal']
        assert config.agents[1].extra_allowed_hosts == []
        assert system.harness == 'codex'


class TestHarborEnvironment:
    def test_keeps_host_tools_and_endpoint_files_while_dropping_assistant_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        values = {
            'PATH': '/usr/bin',
            'BENCH_API_KEY_FILE': '/tmp/example-key',
            'DOCKER_HOST': 'unix:///tmp/example.sock',
            'XDG_DATA_HOME': '/tmp/example-data',
            'OPENAI_API_KEY': 'host-secret',
            'ANTHROPIC_CUSTOM_HEADERS': 'x-host: host-secret',
            'CLAUDE_CODE_MAX_TURNS': '1',
        }
        for name, value in values.items():
            monkeypatch.setenv(name, value)

        environment = jobs.harbor_environment()

        for name in ('PATH', 'BENCH_API_KEY_FILE', 'DOCKER_HOST', 'XDG_DATA_HOME'):
            assert environment[name] == values[name]
        for name in (
            'OPENAI_API_KEY',
            'ANTHROPIC_CUSTOM_HEADERS',
            'CLAUDE_CODE_MAX_TURNS',
        ):
            assert name not in environment
