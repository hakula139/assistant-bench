import asyncio
import base64
from contextlib import suppress
from pathlib import Path
from typing import Any

import filehost


def readme() -> str:
    return Path('README.md').read_text()


def python_api_serves_files(root: str, names: list[str]) -> dict[str, Any]:
    """Serve `/data.bin` from `root` through `handle` and through a `Server`.

    The bodies are base64, and only `exports` is present when a name is missing.
    """
    exports = {name: hasattr(filehost, name) for name in names}
    if not all(exports.values()):
        return {'exports': exports}
    config = filehost.ServerConfig(Path(root), port=0)
    request = filehost.Request('GET', '/data.bin', 'HTTP/1.1', filehost.Headers())

    response = filehost.handle(request, config)
    received = asyncio.run(fetch_through_server(config))
    return {
        'exports': exports,
        'status': response.status,
        'body': encode(read_body(response.body)),
        'received': encode(received),
    }


async def fetch_through_server(config: filehost.ServerConfig) -> bytes:
    server = filehost.Server(config)
    port = await server.start()
    serving = asyncio.create_task(server.serve_forever())
    try:
        async with asyncio.timeout(10):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(
                b'GET /data.bin HTTP/1.1\r\n'
                b'Host: localhost\r\n'
                b'Connection: close\r\n\r\n'
            )
            received = await reader.read()
            writer.close()
            await writer.wait_closed()
    finally:
        serving.cancel()
        with suppress(asyncio.CancelledError):
            await serving
    return received


def read_body(body: bytes | filehost.FileBody) -> bytes:
    if isinstance(body, bytes):
        return body
    with body.path.open('rb') as file:
        file.seek(body.offset)
        return file.read(body.length)


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode()
