import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import openai
import pytest
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from assistant_bench.harnesses.generate import Generate
from support import StubEndpoint, completion


def run_agent(
    endpoint: StubEndpoint, logs_dir: Path, **options: Any
) -> tuple[AgentContext, Path]:
    defaults: dict[str, Any] = {
        'system': 'acme-large',
        'fingerprint': 'f1',
        'max_output_tokens': 64,
    }
    agent = Generate(
        logs_dir=logs_dir,
        model_name='acme/large',
        extra_env=endpoint.env,
        **(defaults | options),
    )
    context = AgentContext()
    asyncio.run(agent.run('Greet me.', MagicMock(spec=BaseEnvironment), context))
    return context, logs_dir / 'output.txt'


pytestmark = pytest.mark.usefixtures('export')


class TestRun:
    def test_sends_one_request_and_records_the_reply(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        context, output = run_agent(endpoint, tmp_path, effort='high')

        (request,) = endpoint.requests
        assert request.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-key'
        assert request.headers['x-trace'] == 'bench'
        assert request.body == {
            'model': 'acme/large',
            'messages': [{'role': 'user', 'content': 'Greet me.'}],
            'max_completion_tokens': 64,
            'reasoning_effort': 'high',
        }
        assert output.read_text() == 'Hello there.'
        assert (
            context.n_input_tokens,
            context.n_cache_tokens,
            context.n_output_tokens,
        ) == (120, 100, 40)
        assert context.metadata is not None
        latency = context.metadata.pop('latency_sec')
        assert isinstance(latency, float)
        assert latency >= 0
        assert context.metadata == {
            'reasoning_tokens': 25,
            'finish_reason': 'stop',
            'response_model': 'large-2026',
            'request_id': 'req-1',
            'nixos_config_rev': '0123456789abcdef0123456789abcdef01234567',
        }

    def test_omits_effort_and_prepends_a_system_prompt(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        run_agent(endpoint, tmp_path, system_prompt='Be brief.')

        (request,) = endpoint.requests
        assert 'reasoning_effort' not in request.body
        assert request.body['messages'] == [
            {'role': 'system', 'content': 'Be brief.'},
            {'role': 'user', 'content': 'Greet me.'},
        ]

    def test_keeps_a_reply_cut_off_by_the_token_limit(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        endpoint.response = completion(content='Hello th', finish_reason='length')

        context, output = run_agent(endpoint, tmp_path)

        assert output.read_text() == 'Hello th'
        assert context.metadata is not None
        assert context.metadata['finish_reason'] == 'length'

    def test_counts_absent_token_details_as_unknown_reasoning_and_no_cache(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        endpoint.response = completion(
            usage={'prompt_tokens': 22, 'completion_tokens': 31, 'total_tokens': 53}
        )

        context, _ = run_agent(endpoint, tmp_path)

        assert context.n_cache_tokens == 0
        assert context.metadata is not None
        assert context.metadata['reasoning_tokens'] is None

    def test_writes_an_empty_output_for_a_reply_without_content(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        endpoint.response = completion(content=None, finish_reason='length')

        _, output = run_agent(endpoint, tmp_path)

        assert output.read_text() == ''

    def test_raises_on_a_server_error_without_retrying(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        endpoint.status = 503
        endpoint.response = {'error': {'message': 'unavailable'}}

        with pytest.raises(openai.InternalServerError):
            run_agent(endpoint, tmp_path)

        assert len(endpoint.requests) == 1
        assert not (tmp_path / 'output.txt').exists()


class TestPreflight:
    OPTIONS = {
        'system': 's',
        'fingerprint': 'f',
        'max_output_tokens': 1,
    }

    def test_accepts_endpoint_variables_from_the_agent_env(
        self, endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv('BENCH_API_BASE_URL', raising=False)
        monkeypatch.delenv('BENCH_API_KEY_FILE', raising=False)

        Generate.preflight(self.OPTIONS, endpoint.env)

    def test_names_each_missing_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ('BENCH_API_BASE_URL', 'BENCH_API_KEY_FILE', 'BENCH_NIXOS_CONFIG'):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(ValueError) as error:
            Generate.preflight(self.OPTIONS, {})

        assert str(error.value) == (
            'BENCH_API_BASE_URL, BENCH_API_KEY_FILE, BENCH_NIXOS_CONFIG unset. '
            'Copy .env.example to .env, and run inside `nix develop`.'
        )

    def test_rejects_an_unknown_option(self, endpoint: StubEndpoint) -> None:
        with pytest.raises(ValueError, match="Unknown option 'temperature'"):
            Generate.preflight(
                self.OPTIONS | {'temperature': 0},
                endpoint.env,
            )
