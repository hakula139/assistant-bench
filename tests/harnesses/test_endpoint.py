import asyncio
import os
import ssl
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from assistant_bench.catalog import EXPORT_VAR
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

    def test_blocks_non_loopback_requests_before_network_io(self) -> None:
        async def request() -> None:
            async with httpx.AsyncClient() as client:
                await client.get('https://example.com/v1/responses')

        with pytest.raises(pytest.fail.Exception, match='non-loopback.*example.com'):
            asyncio.run(request())


class TestEndpoint:
    class TestHttpClient:
        def test_authenticates_the_configured_origin_with_its_private_headers(
            self, endpoint: StubEndpoint
        ) -> None:
            configured = Endpoint.read(endpoint.env.get)

            async def request() -> httpx.Response:
                async with configured.http_client() as client:
                    return await client.post(f'{endpoint.url}/v1/responses', json={})

            reply = asyncio.run(request())

            assert reply.status_code == 200
            (sent,) = endpoint.requests
            assert sent.headers['authorization'] == 'Bearer test-key'
            assert sent.headers['x-trace'] == 'bench'

        @pytest.mark.parametrize('origin_part', ['scheme', 'host', 'port'])
        def test_rejects_another_origin_before_reading_the_key_or_sending(
            self, endpoint: StubEndpoint, origin_part: str
        ) -> None:
            configured = Endpoint.read(endpoint.env.get)
            url = httpx.URL(f'{endpoint.url}/v1/responses')
            targets = {
                'scheme': url.copy_with(scheme='https'),
                'host': url.copy_with(host='::1'),
                'port': url.copy_with(port=1),
            }
            endpoint.key_file.unlink()

            async def request() -> None:
                async with configured.http_client() as client:
                    await client.post(targets[origin_part], json={})

            with pytest.raises(ValueError, match='cannot authenticate another origin'):
                asyncio.run(request())

            assert endpoint.requests == []

        def test_does_not_follow_a_redirect_to_another_endpoint(
            self, endpoint: StubEndpoint, ipv6_endpoint: StubEndpoint
        ) -> None:
            configured = Endpoint.read(endpoint.env.get)
            endpoint.status = 307
            endpoint.response_headers['Location'] = f'{ipv6_endpoint.url}/v1/responses'

            async def request() -> httpx.Response:
                async with configured.http_client() as client:
                    return await client.post(f'{endpoint.url}/v1/responses', json={})

            reply = asyncio.run(request())

            assert reply.status_code == 307
            assert len(endpoint.requests) == 1
            assert ipv6_endpoint.requests == []


class TestPreflight:
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
