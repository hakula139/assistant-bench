import asyncio
import ssl
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import openai
import pytest
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import ValidationError

from assistant_bench.harnesses.generate import Generate
from support import StubEndpoint, completion


def run_agent(
    endpoint: StubEndpoint,
    logs_dir: Path,
    *,
    env: dict[str, str] | None = None,
    **options: Any,
) -> tuple[AgentContext, Path]:
    defaults: dict[str, Any] = {
        'system': 'acme-large',
        'fingerprint': 'f1',
        'max_output_tokens': 64,
    }
    agent = Generate(
        logs_dir=logs_dir,
        model_name='acme/large',
        extra_env=endpoint.env | (env or {}),
        **(defaults | options),
    )
    context = AgentContext()
    asyncio.run(agent.run('Greet me.', MagicMock(spec=BaseEnvironment), context))
    return context, logs_dir / 'output.txt'


pytestmark = pytest.mark.usefixtures('export')


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

    @pytest.mark.parametrize(
        ('variable', 'value'),
        [
            ('BENCH_API_BASE_URL', 'api.example.com/v1'),
            ('BENCH_API_KEY_FILE', 'missing-key'),
            ('BENCH_API_HEADERS', '{"x-trace": 1}'),
            ('BENCH_API_CA_FILE', 'missing-ca.pem'),
            ('BENCH_API_LOCAL_ADDRESS', 'eth0'),
        ],
    )
    def test_rejects_an_unusable_endpoint_setting(
        self, endpoint: StubEndpoint, variable: str, value: str
    ) -> None:
        with pytest.raises(ValidationError) as error:
            Generate.preflight(self.OPTIONS, endpoint.env | {variable: value})

        assert [e['loc'][0] for e in error.value.errors()] == [variable]
        assert value not in str(error.value)

    def test_rejects_a_ca_bundle_that_is_not_pem(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        ca_file = tmp_path / 'ca.pem'
        ca_file.write_text('not a certificate\n')

        with pytest.raises(ssl.SSLError):
            Generate.preflight(
                self.OPTIONS, endpoint.env | {'BENCH_API_CA_FILE': str(ca_file)}
            )

    def test_treats_an_empty_optional_setting_as_unset(
        self, endpoint: StubEndpoint
    ) -> None:
        empty = dict.fromkeys(
            ('BENCH_API_CA_FILE', 'BENCH_API_HEADERS', 'BENCH_API_LOCAL_ADDRESS'), ''
        )

        Generate.preflight(self.OPTIONS, endpoint.env | empty)


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

    def test_leaves_tokens_unknown_when_the_endpoint_reports_no_usage(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        del endpoint.response['usage']

        context, output = run_agent(endpoint, tmp_path)

        assert output.read_text() == 'Hello there.'
        assert (
            context.n_input_tokens,
            context.n_cache_tokens,
            context.n_output_tokens,
        ) == (None, None, None)
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

    def test_reaches_an_ipv6_endpoint_without_a_local_address(
        self, ipv6_endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        _, output = run_agent(ipv6_endpoint, tmp_path)

        assert output.read_text() == 'Hello there.'
        assert len(ipv6_endpoint.requests) == 1

    def test_binds_connections_to_the_local_address_the_env_names(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        # 192.0.2.1 is reserved for documentation, so no host owns it.
        with pytest.raises(openai.APIConnectionError):
            run_agent(endpoint, tmp_path, env={'BENCH_API_LOCAL_ADDRESS': '192.0.2.1'})
        _, output = run_agent(
            endpoint, tmp_path, env={'BENCH_API_LOCAL_ADDRESS': '127.0.0.1'}
        )

        assert output.read_text() == 'Hello there.'
        assert len(endpoint.requests) == 1

    def test_trusts_the_ca_bundle_the_env_names(
        self, tls_endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        assert tls_endpoint.ca_file is not None

        with pytest.raises(openai.APIConnectionError):
            run_agent(tls_endpoint, tmp_path)
        _, output = run_agent(
            tls_endpoint,
            tmp_path,
            env={'BENCH_API_CA_FILE': str(tls_endpoint.ca_file)},
        )

        assert output.read_text() == 'Hello there.'
        assert len(tls_endpoint.requests) == 1
