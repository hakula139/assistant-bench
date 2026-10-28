import os
import ssl
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import trustme
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from assistant_bench import families, jobs
from assistant_bench.catalog import EXPORT_VAR, NixosConfigExport
from assistant_bench.harnesses.endpoint import ENDPOINT_VARS, SDK_HEADERS_VAR, Endpoint
from assistant_bench.paths import DATA_DIR_VAR
from support import EXPORT_FILE, StubEndpoint, serve_stub


# Set as `bench` sets it, before a test module imports LiteLLM.
os.environ.update(jobs.HARBOR_ENV)

PROXY_VARS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        '--update-fixtures',
        action='store_true',
        help='Rewrite each committed fixture a test finds stale.',
    )


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> None:
    # A fixture one test rewrites is read by tests on other workers, so rewrite in one
    # process. This runs before pytest-xdist's own hook turns `-n` into workers.
    if config.getoption('update_fixtures'):
        config.option.numprocesses = 0


@pytest.fixture
def stale_fixture(request: pytest.FixtureRequest) -> Callable[[Path, str], None]:
    """Fail on a stale committed fixture, or rewrite it under `--update-fixtures`."""

    def handle(path: Path, current: str) -> None:
        __tracebackhide__ = True
        if request.config.getoption('update_fixtures'):
            path.write_text(current)
        else:
            pytest.fail(
                f'{path.name} is stale, so rerun with --update-fixtures '
                'and review the diff'
            )

    return handle


@pytest.fixture(autouse=True)
def loopback_requests_only(monkeypatch: pytest.MonkeyPatch) -> None:
    send = httpx.AsyncClient.send
    send_messages = httpx2.AsyncClient.send

    async def checked_send(
        client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.url.host not in {'127.0.0.1', '::1'}:
            pytest.fail(
                f'A model test attempted a non-loopback request: {request.url.host}'
            )
        return await send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, 'send', checked_send)

    async def checked_messages_send(
        client: httpx2.AsyncClient, request: httpx2.Request, **kwargs: Any
    ) -> httpx2.Response:
        if request.url.host not in {'127.0.0.1', '::1'}:
            pytest.fail(
                f'A model test attempted a non-loopback request: {request.url.host}'
            )
        return await send_messages(client, request, **kwargs)

    monkeypatch.setattr(httpx2.AsyncClient, 'send', checked_messages_send)


@pytest.fixture
def model_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> list[httpx.Request | httpx2.Request]:
    requests: list[httpx.Request | httpx2.Request] = []
    send = httpx.AsyncClient.send
    send_messages = httpx2.AsyncClient.send

    async def record(
        client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        requests.append(request)
        return await send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, 'send', record)

    async def record_messages(
        client: httpx2.AsyncClient, request: httpx2.Request, **kwargs: Any
    ) -> httpx2.Response:
        requests.append(request)
        return await send_messages(client, request, **kwargs)

    monkeypatch.setattr(httpx2.AsyncClient, 'send', record_messages)
    return requests


@pytest.fixture
def endpoint_clients(monkeypatch: pytest.MonkeyPatch) -> list[AsyncOpenAI]:
    clients: list[AsyncOpenAI] = []
    build = Endpoint.client

    def record(endpoint: Endpoint) -> AsyncOpenAI:
        client = build(endpoint)
        clients.append(client)
        return client

    monkeypatch.setattr(Endpoint, 'client', record)
    return clients


@pytest.fixture
def messages_clients(monkeypatch: pytest.MonkeyPatch) -> list[AsyncAnthropic]:
    clients: list[AsyncAnthropic] = []
    build = Endpoint.messages_client

    def record(endpoint: Endpoint) -> AsyncAnthropic:
        client = build(endpoint)
        clients.append(client)
        return client

    monkeypatch.setattr(Endpoint, 'messages_client', record)
    return clients


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate data, endpoint settings and system proxies."""
    path = tmp_path / 'data'
    monkeypatch.setenv(DATA_DIR_VAR, str(path))
    monkeypatch.chdir(tmp_path)
    for name in ENDPOINT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(SDK_HEADERS_VAR, raising=False)
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv('NO_PROXY', '*')
    return path


@pytest.fixture
def families_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / 'families'
    monkeypatch.setattr(families, 'FAMILIES_DIR', directory)
    return directory


@pytest.fixture
def export(monkeypatch: pytest.MonkeyPatch) -> NixosConfigExport:
    monkeypatch.setenv(EXPORT_VAR, str(EXPORT_FILE))
    return NixosConfigExport.model_validate_json(EXPORT_FILE.read_bytes())


@pytest.fixture(params=[False, True], ids=['agent-only', 'conflicting-process'])
def agent_export(
    request: pytest.FixtureRequest,
    export: NixosConfigExport,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[dict[str, str], NixosConfigExport]:
    overridden = export.model_copy(
        update={'rev': 'fedcba9876543210fedcba9876543210fedcba98'}
    )
    path = tmp_path / 'agent-export.json'
    path.write_text(overridden.model_dump_json(by_alias=True))
    if not request.param:
        monkeypatch.delenv(EXPORT_VAR)

    return {EXPORT_VAR: str(path)}, overridden


@pytest.fixture
def endpoint(tmp_path: Path) -> Iterator[StubEndpoint]:
    yield from serve_stub(tmp_path)


@pytest.fixture
def tls_endpoint(tmp_path: Path) -> Iterator[StubEndpoint]:
    """Serve the stub endpoint over TLS with a test CA's certificate for 127.0.0.1."""
    ca = trustme.CA()
    ca_file = tmp_path / 'ca.pem'
    ca.cert_pem.write_to_path(str(ca_file))
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert('127.0.0.1').configure_cert(context)
    yield from serve_stub(tmp_path, context, ca_file)


@pytest.fixture
def ipv6_endpoint(tmp_path: Path) -> Iterator[StubEndpoint]:
    yield from serve_stub(tmp_path, host='::1')
