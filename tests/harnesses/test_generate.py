import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import openai
import pytest
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from assistant_bench.catalog import EXPORT_VAR, NixosConfigExport
from assistant_bench.generation import generation_version
from assistant_bench.harnesses import options
from assistant_bench.harnesses.endpoint import CA_FILE_VAR, LOCAL_ADDRESS_VAR
from assistant_bench.harnesses.generate import OUTPUT_FILE, Generate
from support import StubEndpoint, completion, make_system


pytestmark = pytest.mark.usefixtures('export')

OPTIONS: dict[str, Any] = {'system': 's', 'fingerprint': 'f', 'max_output_tokens': 1}


def run_agent(
    endpoint: StubEndpoint,
    logs_dir: Path,
    *,
    env: dict[str, str] | None = None,
    **options: Any,
) -> tuple[AgentContext, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
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
    return context, logs_dir / OUTPUT_FILE


class TestGenerate:
    class TestVersion:
        def test_reports_the_generation_protocol(self, tmp_path: Path) -> None:
            agent = Generate(logs_dir=tmp_path, model_name='acme/large', **OPTIONS)

            version = agent.version()

            assert version == generation_version('generate')

    class TestPreflight:
        def test_rejects_an_unknown_option(self, endpoint: StubEndpoint) -> None:
            with pytest.raises(ValueError, match="Unknown option 'temperature'"):
                Generate.preflight(OPTIONS | {'temperature': 0}, endpoint.env)

        def test_checks_the_endpoint_variables(
            self, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.delenv(EXPORT_VAR)

            with pytest.raises(ValueError, match='^Set `BENCH_API_BASE_URL`'):
                Generate.preflight(OPTIONS, {})

    class TestRun:
        def test_preserves_a_truncated_attempt_when_a_new_protocol_regenerates_it(
            self, endpoint: StubEndpoint, tmp_path: Path
        ) -> None:
            bounded = make_system(max_output_tokens=16000, context_window=131072)
            endpoint.response = completion(
                content=None,
                finish_reason='length',
                usage={
                    'prompt_tokens': 100,
                    'completion_tokens': 16000,
                    'total_tokens': 16100,
                    'completion_tokens_details': {'reasoning_tokens': 16000},
                },
            )

            first, first_output = run_agent(
                endpoint, tmp_path / 'bounded', **options.generate(bounded)
            )

            assert first_output.read_text() == ''
            assert first.n_output_tokens == 16000
            assert first.metadata is not None
            assert first.metadata['finish_reason'] == 'length'
            assert first.metadata['reasoning_tokens'] == 16000
            assert len(endpoint.requests) == 1

            generous = bounded.model_copy(
                update={'max_output_tokens': 65536, 'context_window': 262144}
            )
            endpoint.response = completion(content='A complete answer.')

            second, second_output = run_agent(
                endpoint, tmp_path / 'generous', **options.generate(generous)
            )

            assert [
                request.body['max_completion_tokens'] for request in endpoint.requests
            ] == [16000, 65536]
            assert generous.fingerprint != bounded.fingerprint
            assert second_output.read_text() == 'A complete answer.'
            assert second.metadata is not None
            assert second.metadata['finish_reason'] == 'stop'
            assert first_output.read_text() == ''
            assert first.metadata['finish_reason'] == 'length'

        def test_uses_the_same_agent_export_for_preflight_and_metadata(
            self,
            agent_export: tuple[dict[str, str], NixosConfigExport],
            endpoint: StubEndpoint,
            tmp_path: Path,
        ) -> None:
            env, export = agent_export
            Generate.preflight(OPTIONS, endpoint.env | env)

            context, output = run_agent(endpoint, tmp_path, env=env)

            assert output.read_text() == 'Hello there.'
            assert len(endpoint.requests) == 1
            assert context.metadata is not None
            assert context.metadata['nixos_config_rev'] == export.rev

        @pytest.mark.parametrize('effort', ['high', 'max'])
        def test_sends_one_request_and_records_the_reply(
            self, endpoint: StubEndpoint, tmp_path: Path, effort: str
        ) -> None:
            context, output = run_agent(endpoint, tmp_path, effort=effort)

            (request,) = endpoint.requests
            assert request.path == '/v1/chat/completions'
            assert request.headers['authorization'] == 'Bearer test-key'
            assert request.headers['x-trace'] == 'bench'
            assert request.body == {
                'model': 'acme/large',
                'messages': [{'role': 'user', 'content': 'Greet me.'}],
                'max_completion_tokens': 64,
                'reasoning_effort': effort,
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
            assert not (tmp_path / OUTPUT_FILE).exists()

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
                run_agent(endpoint, tmp_path, env={LOCAL_ADDRESS_VAR: '192.0.2.1'})
            _, output = run_agent(
                endpoint, tmp_path, env={LOCAL_ADDRESS_VAR: '127.0.0.1'}
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
                env={CA_FILE_VAR: str(tls_endpoint.ca_file)},
            )

            assert output.read_text() == 'Hello there.'
            assert len(tls_endpoint.requests) == 1
