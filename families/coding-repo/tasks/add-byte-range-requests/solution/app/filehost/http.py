"""HTTP/1.1 message parsing and serialization."""

import asyncio
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .dates import format_http_date


MAX_LINE_BYTES = 8192
MAX_HEADER_FIELDS = 100
MAX_DISCARDED_BODY = 64 * 1024
CHUNK_BYTES = 64 * 1024

REASONS = {
    200: 'OK',
    206: 'Partial Content',
    304: 'Not Modified',
    400: 'Bad Request',
    404: 'Not Found',
    405: 'Method Not Allowed',
    412: 'Precondition Failed',
    413: 'Content Too Large',
    416: 'Range Not Satisfiable',
    431: 'Request Header Fields Too Large',
    500: 'Internal Server Error',
    501: 'Not Implemented',
    505: 'HTTP Version Not Supported',
}


class BadRequest(Exception):
    """A request the server rejects before handling it."""

    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        super().__init__(reason)


class Headers:
    """Header fields in order, looked up case-insensitively."""

    def __init__(self, fields: Iterable[tuple[str, str]] = ()) -> None:
        self._fields = list(fields)

    def get(self, name: str) -> str | None:
        """Return every field named `name` joined by commas, or `None`."""
        values = self.get_all(name)
        return ', '.join(values) if values else None

    def get_all(self, name: str) -> list[str]:
        folded = name.lower()
        return [value for key, value in self._fields if key.lower() == folded]

    def add(self, name: str, value: str) -> None:
        self._fields.append((name, value))

    def remove(self, name: str) -> None:
        folded = name.lower()
        self._fields = [field for field in self._fields if field[0].lower() != folded]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and bool(self.get_all(name))

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._fields)


@dataclass(frozen=True)
class Request:
    method: str
    target: str
    version: str
    headers: Headers

    @property
    def keep_alive(self) -> bool:
        options = {
            option.strip().lower()
            for option in (self.headers.get('Connection') or '').split(',')
        }
        if self.version == 'HTTP/1.0':
            return 'keep-alive' in options
        return 'close' not in options


@dataclass(frozen=True)
class FileBody:
    """`length` bytes of the file at `path`, starting at byte `offset`."""

    path: Path
    offset: int
    length: int


type Body = bytes | FileBody


@dataclass
class Response:
    status: int
    headers: Headers = field(default_factory=Headers)
    body: Body = b''

    @property
    def content_length(self) -> int:
        return self.body.length if isinstance(self.body, FileBody) else len(self.body)


async def read_request(reader: asyncio.StreamReader) -> Request | None:
    """Read one request head, or return `None` when the client closed cleanly."""
    line = await _read_line(reader)
    if line is None:
        return None
    if not line:
        line = await _read_line(reader)
        if line is None:
            return None

    try:
        method, target, version = line.decode('ascii').split(' ')
    except UnicodeDecodeError, ValueError:
        raise BadRequest(400, 'Malformed request line.') from None

    if version not in {'HTTP/1.0', 'HTTP/1.1'}:
        raise BadRequest(505, f'Unsupported version {version}.')

    headers = Headers()
    while True:
        field_line = await _read_line(reader)
        if field_line is None:
            raise BadRequest(400, 'Incomplete request head.')
        if not field_line:
            break
        if len(list(headers)) == MAX_HEADER_FIELDS:
            raise BadRequest(431, 'Too many header fields.')

        name, colon, value = field_line.decode('latin-1').partition(':')
        if not colon or not name or name != name.strip() or field_line[:1] in b' \t':
            raise BadRequest(400, 'Malformed header field.')
        headers.add(name, value.strip(' \t'))

    if version == 'HTTP/1.1' and 'Host' not in headers:
        raise BadRequest(400, 'Missing Host header field.')

    await _discard_body(reader, headers)
    return Request(method, target, version, headers)


async def write_response(
    writer: asyncio.StreamWriter,
    response: Response,
    *,
    include_body: bool,
    keep_alive: bool,
) -> None:
    """Send `response`, leaving out its body bytes when `include_body` is false."""
    lines = [f'HTTP/1.1 {response.status} {REASONS[response.status]}']
    lines.extend(f'{name}: {value}' for name, value in response.headers)
    if response.status != 304:
        lines.append(f'Content-Length: {response.content_length}')
    lines.append(f'Date: {format_http_date(time.time())}')
    lines.append(f'Connection: {"keep-alive" if keep_alive else "close"}')
    writer.write(('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1'))

    if include_body and response.status != 304:
        body = response.body
        if isinstance(body, bytes):
            writer.write(body)
        else:
            await _write_file(writer, body)

    await writer.drain()


async def _read_line(reader: asyncio.StreamReader) -> bytes | None:
    try:
        line = await reader.readuntil(b'\n')
    except asyncio.IncompleteReadError as error:
        if error.partial:
            raise BadRequest(400, 'Incomplete line.') from None
        return None
    except asyncio.LimitOverrunError:
        raise BadRequest(431, 'Line too long.') from None

    if len(line) > MAX_LINE_BYTES:
        raise BadRequest(431, 'Line too long.')
    return line.removesuffix(b'\n').removesuffix(b'\r')


async def _discard_body(reader: asyncio.StreamReader, headers: Headers) -> None:
    if 'Transfer-Encoding' in headers:
        raise BadRequest(501, 'Request bodies are not supported.')

    length = headers.get('Content-Length')
    if length is None:
        return
    if not length.isdigit():
        raise BadRequest(400, 'Invalid Content-Length.')
    if int(length) > MAX_DISCARDED_BODY:
        raise BadRequest(413, 'Request body too large.')

    try:
        await reader.readexactly(int(length))
    except asyncio.IncompleteReadError:
        raise BadRequest(400, 'Incomplete request body.') from None


async def _write_file(writer: asyncio.StreamWriter, body: FileBody) -> None:
    with body.path.open('rb') as file:
        file.seek(body.offset)
        remaining = body.length
        while remaining:
            chunk = file.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                raise OSError(f'{body.path} shrank while it was being sent.')
            writer.write(chunk)
            await writer.drain()
            remaining -= len(chunk)
