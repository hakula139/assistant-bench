import asyncio
from importlib import metadata
from pathlib import Path
from typing import Protocol, runtime_checkable
from unittest.mock import MagicMock

import pytest
from harbor.agents.terminus_2.terminus_2 import Terminus2
from harbor.environments.base import BaseEnvironment
from harbor.llms.base import LLMResponse
from harbor.models.agent.context import AgentContext

from assistant_bench.catalog import EXPORT_VAR, NixosConfigExport
from assistant_bench.generation import generation_version
from assistant_bench.harnesses import options
from assistant_bench.harnesses.terminus import Terminus
from support import StubEndpoint, make_system


pytestmark = pytest.mark.usefixtures('export')

OPTIONS = {'system': 's', 'fingerprint': 'f'}


@runtime_checkable
class NativeClient(Protocol):
    def get_model_context_limit(self) -> int: ...
    def get_model_output_limit(self) -> int | None: ...
    async def call(self, prompt: str, **kwargs: object) -> LLMResponse: ...


class NativeProbe(Terminus):
    @property
    def client(self) -> NativeClient:
        assert isinstance(self._llm, NativeClient)
        return self._llm

    async def request(self, prompt: str) -> LLMResponse:
        return await self.client.call(prompt, **self._llm_call_kwargs)


class TestTerminus:
    def test_keeps_each_systems_context_reserve_when_routes_are_shared(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        first = make_system(
            route='acme/shared-capacity',
            harness='terminus-2',
            max_output_tokens=64,
            context_window=8192,
        )
        second = first.model_copy(
            update={'name': 'other', 'max_output_tokens': 128, 'context_window': 16384}
        )
        first_agent = NativeProbe(
            logs_dir=tmp_path / 'first',
            model_name=f'openai/{first.route}',
            extra_env=endpoint.env,
            **options.terminus(first),
        )

        second_agent = NativeProbe(
            logs_dir=tmp_path / 'second',
            model_name=f'openai/{second.route}',
            extra_env=endpoint.env,
            **options.terminus(second),
        )

        assert first_agent.client.get_model_context_limit() == 8128
        assert first_agent.client.get_model_output_limit() == 64
        assert second_agent.client.get_model_context_limit() == 16256
        assert second_agent.client.get_model_output_limit() == 128
        assert endpoint.requests == []

    def test_uses_native_context_management_without_an_artificial_turn_limit(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        system = make_system(
            route='acme/context-check',
            harness='terminus-2',
            max_output_tokens=32768,
            context_window=131072,
        )
        agent = NativeProbe(
            logs_dir=tmp_path,
            model_name=f'openai/{system.route}',
            extra_env=endpoint.env,
            **options.terminus(system),
        )

        assert agent.client.get_model_context_limit() == 98304
        assert agent.client.get_model_output_limit() == 32768
        assert agent.options.max_turns is None
        assert agent.version() == generation_version('terminus-2')
        assert endpoint.requests == []

    class TestPreflight:
        def test_rejects_an_effort_the_agent_does_not_accept(
            self, endpoint: StubEndpoint
        ) -> None:
            with pytest.raises(ValueError, match='reasoning_effort'):
                Terminus.preflight(
                    OPTIONS | {'reasoning_effort': 'turbo'}, endpoint.env
                )

        def test_checks_the_endpoint_variables(
            self, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.delenv(EXPORT_VAR)

            with pytest.raises(ValueError, match='^Set `BENCH_API_BASE_URL`'):
                Terminus.preflight(OPTIONS, {})

    class TestRun:
        @pytest.mark.parametrize('effort', ['high', 'max'])
        def test_forwards_the_configured_quality_settings_through_the_native_client(
            self, endpoint: StubEndpoint, tmp_path: Path, effort: str
        ) -> None:
            system = make_system(
                route='acme/native-quality',
                harness='terminus-2',
                effort=effort,
                max_output_tokens=32768,
                context_window=131072,
            )
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **options.terminus(system),
            )

            response = asyncio.run(agent.request('Greet me.'))

            (request,) = endpoint.requests
            assert request.body['model'] == system.route
            assert request.body['reasoning_effort'] == effort
            assert request.body['max_completion_tokens'] == 32768
            assert request.headers['authorization'] == 'Bearer test-key'
            assert request.headers['x-trace'] == 'bench'
            assert response.content == 'Hello there.'
            assert response.usage is not None
            assert response.usage.prompt_tokens == 120
            assert response.usage.completion_tokens == 40

        def test_keeps_its_metadata_when_the_parent_raises(
            self,
            agent_export: tuple[dict[str, str], NixosConfigExport],
            endpoint: StubEndpoint,
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            env, export = agent_export
            kwargs = options.terminus(make_system(harness='terminus-2'))
            Terminus.preflight(kwargs, endpoint.env | env)
            agent = Terminus(
                logs_dir=tmp_path,
                model_name='openai/acme/large',
                extra_env=endpoint.env | env,
                **kwargs,
            )
            context = AgentContext()

            async def fail(
                self: Terminus2,
                instruction: str,
                environment: BaseEnvironment,
                context: AgentContext,
            ) -> None:
                context.metadata = {'n_episodes': 1}
                raise RuntimeError('agent failed')

            monkeypatch.setattr(Terminus2, 'run', fail)

            with pytest.raises(RuntimeError, match='agent failed'):
                asyncio.run(agent.run('Go.', MagicMock(spec=BaseEnvironment), context))

            assert context.metadata == {
                'n_episodes': 1,
                'nixos_config_rev': export.rev,
                'harbor_version': metadata.version('harbor'),
            }
