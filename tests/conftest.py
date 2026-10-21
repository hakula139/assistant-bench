import json
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import trustme

from assistant_bench import families
from assistant_bench.catalog import EXPORT_VAR, NixosConfigExport
from support import EXPORT_FILE, Request, StubEndpoint


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the data directory at a scratch path and leaves any `.env` unread."""
    home = tmp_path / 'xdg-data'
    monkeypatch.setenv('XDG_DATA_HOME', str(home))
    monkeypatch.chdir(tmp_path)
    return home / 'assistant-bench'


@pytest.fixture
def families_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point family discovery at a scratch `families/` directory."""
    directory = tmp_path / 'families'
    monkeypatch.setattr(families, 'FAMILIES_DIR', directory)
    return directory


@pytest.fixture
def export(monkeypatch: pytest.MonkeyPatch) -> NixosConfigExport:
    monkeypatch.setenv(EXPORT_VAR, str(EXPORT_FILE))
    return NixosConfigExport.model_validate_json(EXPORT_FILE.read_bytes())


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


def serve_stub(
    tmp_path: Path,
    context: ssl.SSLContext | None = None,
    ca_file: Path | None = None,
) -> Iterator[StubEndpoint]:
    key_file = tmp_path / 'api-key'
    key_file.write_text('test-key\n')
    stub: StubEndpoint

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers['Content-Length'])
            stub.requests.append(
                Request(
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=json.loads(self.rfile.read(length)),
                )
            )
            payload = json.dumps(stub.response).encode()
            self.send_response(stub.status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('x-request-id', 'req-1')
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    scheme = 'http'
    if context is not None:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = 'https'
    stub = StubEndpoint(
        url=f'{scheme}://127.0.0.1:{server.server_port}',
        key_file=key_file,
        ca_file=ca_file,
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True
    )
    thread.start()
    yield stub
    server.shutdown()
    thread.join()
