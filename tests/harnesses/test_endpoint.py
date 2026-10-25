import asyncio
import os
import ssl
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import httpx2
import pytest
from pydantic import ValidationError

from assistant_bench.catalog import EXPORT_VAR, Api
from assistant_bench.harnesses.endpoint import (
    BASE_URL_VAR,
    CA_FILE_VAR,
    HEADERS_VAR,
    KEY_FILE_VAR,
    LOCAL_ADDRESS_VAR,
    Endpoint,
    preflight,
)
from support import StubEndpoint


pytestmark = pytest.mark.usefixtures('export')


class TestModelRequests:
    def test_imports_the_cli_without_loading_a_local_dotenv(self) -> None:
        environment = dict(os.environ)
        environment.pop('LITELLM_MODE', None)
        script = textwrap.dedent(
            """\
            import socket

            import dotenv

            def unexpected(*args, **kwargs):
                raise AssertionError('Import attempted dotenv loading or network I/O')

            dotenv.load_dotenv = unexpected
            socket.socket.connect = unexpected
            socket.socket.connect_ex = unexpected
            socket.getaddrinfo = unexpected

            import assistant_bench.cli
            """
        )

        completed = subprocess.run(
            [sys.executable, '-c', script],
            env=environment,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    @pytest.mark.parametrize('client_type', [httpx.AsyncClient, httpx2.AsyncClient])
    def test_blocks_non_loopback_requests_before_network_io(
        self, client_type: type[httpx.AsyncClient] | type[httpx2.AsyncClient]
    ) -> None:
        async def request() -> None:
            async with client_type() as client:
                await client.get('https://example.com/v1/responses')

        with pytest.raises(pytest.fail.Exception, match='non-loopback.*example.com'):
            asyncio.run(request())


class TestEndpoint:
    class TestHttpClient:
        @pytest.mark.parametrize(
            'api', ['openai-chat-completions', 'anthropic-messages']
        )
        def test_authenticates_the_configured_origin_with_its_private_headers(
            self,
            endpoint: StubEndpoint,
            model_requests: list[httpx.Request | httpx2.Request],
            api: Api,
        ) -> None:
            configured = Endpoint.read(endpoint.env.get, api)
            build = (
                configured.messages_http_client
                if api == 'anthropic-messages'
                else configured.http_client
            )

            async def request() -> httpx.Response | httpx2.Response:
                async with build() as client:
                    return await client.post(f'{endpoint.url}/v1/responses', json={})

            reply = asyncio.run(request())

            assert reply.status_code == 200
            (sent,) = endpoint.requests
            assert sent.headers['authorization'] == 'Bearer test-key'
            assert sent.headers['x-trace'] == 'bench'
            (outgoing,) = model_requests
            assert outgoing.extensions['timeout'] == {
                'connect': 5.0,
                'read': None,
                'write': 600.0,
                'pool': 600.0,
            }

        @pytest.mark.parametrize('origin_part', ['scheme', 'host', 'port'])
        @pytest.mark.parametrize(
            'api', ['openai-chat-completions', 'anthropic-messages']
        )
        def test_rejects_another_origin_before_reading_the_key_or_sending(
            self, endpoint: StubEndpoint, origin_part: str, api: Api
        ) -> None:
            configured = Endpoint.read(endpoint.env.get, api)
            build = (
                configured.messages_http_client
                if api == 'anthropic-messages'
                else configured.http_client
            )
            url = httpx.URL(f'{endpoint.url}/v1/responses')
            targets = {
                'scheme': url.copy_with(scheme='https'),
                'host': url.copy_with(host='::1'),
                'port': url.copy_with(port=1),
            }
            endpoint.key_file.unlink()

            async def request() -> None:
                async with build() as client:
                    await client.post(str(targets[origin_part]), json={})

            with pytest.raises(ValueError, match='cannot authenticate another origin'):
                asyncio.run(request())

            assert endpoint.requests == []

        @pytest.mark.parametrize(
            'api', ['openai-chat-completions', 'anthropic-messages']
        )
        def test_does_not_follow_a_redirect_to_another_endpoint(
            self, endpoint: StubEndpoint, ipv6_endpoint: StubEndpoint, api: Api
        ) -> None:
            configured = Endpoint.read(endpoint.env.get, api)
            build = (
                configured.messages_http_client
                if api == 'anthropic-messages'
                else configured.http_client
            )
            endpoint.status = 307
            endpoint.response_headers['Location'] = f'{ipv6_endpoint.url}/v1/responses'

            async def request() -> httpx.Response | httpx2.Response:
                async with build() as client:
                    return await client.post(f'{endpoint.url}/v1/responses', json={})

            reply = asyncio.run(request())

            assert reply.status_code == 307
            assert len(endpoint.requests) == 1
            assert ipv6_endpoint.requests == []

    class TestMessagesHttpClient:
        @pytest.mark.parametrize(
            ('fixture', 'address'),
            [('tls_endpoint', '127.0.0.1'), ('ipv6_endpoint', '::1')],
            ids=['trusted-ca-ipv4', 'ipv6-bind'],
        )
        def test_uses_the_configured_trust_and_local_address(
            self, request: pytest.FixtureRequest, fixture: str, address: str
        ) -> None:
            endpoint: StubEndpoint = request.getfixturevalue(fixture)
            env = endpoint.env | {LOCAL_ADDRESS_VAR: address}
            if endpoint.ca_file:
                env[CA_FILE_VAR] = str(endpoint.ca_file)
            configured = Endpoint.read(env.get, 'anthropic-messages')

            async def send(endpoint_config: Endpoint) -> httpx2.Response:
                async with endpoint_config.messages_http_client() as client:
                    return await client.post(
                        f'{endpoint.url}/anthropic/v1/messages', json={}
                    )

            if endpoint.ca_file:
                untrusted = Endpoint.read(endpoint.env.get, 'anthropic-messages')
                with pytest.raises(httpx2.ConnectError):
                    asyncio.run(send(untrusted))
            reply = asyncio.run(send(configured))

            assert reply.status_code == 200
            (sent,) = endpoint.requests
            assert sent.headers['authorization'] == 'Bearer test-key'


class TestPreflight:
    def test_rejects_sdk_ambient_headers_before_native_requests(
        self, endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            'ANTHROPIC_CUSTOM_HEADERS',
            'x-ambient-header: ambient-value\nanthropic-beta: ambient-beta',
        )
        configured = Endpoint.read(endpoint.env.get, 'anthropic-messages')

        with pytest.raises(ValueError, match='ANTHROPIC_CUSTOM_HEADERS'):
            preflight(endpoint.env, 'anthropic-messages')
        with pytest.raises(ValueError, match='ANTHROPIC_CUSTOM_HEADERS'):
            configured.messages_client()

        assert endpoint.requests == []

    def test_requires_the_exact_native_base_without_falling_back_to_openai(
        self, endpoint: StubEndpoint
    ) -> None:
        env = endpoint.env.copy()
        env.pop('BENCH_ANTHROPIC_BASE_URL')

        with pytest.raises(ValueError, match='^Set `BENCH_ANTHROPIC_BASE_URL`'):
            preflight(env, 'anthropic-messages')

        assert endpoint.requests == []

    def test_accepts_endpoint_variables_from_the_agent_env(
        self, endpoint: StubEndpoint
    ) -> None:
        preflight(endpoint.env)

    def test_names_each_missing_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(EXPORT_VAR)

        with pytest.raises(ValueError) as error:
            preflight({})

        assert str(error.value) == (
            'Set `BENCH_API_BASE_URL`, `BENCH_API_KEY_FILE`, `BENCH_NIXOS_CONFIG`. '
            'Copy `.env.example` to `.env`, and run inside `nix develop`.'
        )

    @pytest.mark.parametrize(
        ('variable', 'value'),
        [
            (BASE_URL_VAR, 'api.example.com/v1'),
            (KEY_FILE_VAR, 'missing-key'),
            (HEADERS_VAR, '{"x-trace": 1}'),
            (CA_FILE_VAR, 'missing-ca.pem'),
            (LOCAL_ADDRESS_VAR, 'eth0'),
        ],
    )
    def test_rejects_an_unusable_endpoint_setting(
        self, endpoint: StubEndpoint, variable: str, value: str
    ) -> None:
        with pytest.raises(ValidationError) as error:
            preflight(endpoint.env | {variable: value})

        assert [e['loc'][0] for e in error.value.errors()] == [variable]
        assert value not in str(error.value)

    def test_rejects_a_ca_bundle_that_is_not_pem(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        ca_file = tmp_path / 'ca.pem'
        ca_file.write_text('not a certificate\n')

        with pytest.raises(ssl.SSLError):
            preflight(endpoint.env | {CA_FILE_VAR: str(ca_file)})

    def test_treats_an_empty_optional_setting_as_unset(
        self, endpoint: StubEndpoint
    ) -> None:
        empty = dict.fromkeys((CA_FILE_VAR, HEADERS_VAR, LOCAL_ADDRESS_VAR), '')

        preflight(endpoint.env | empty)
