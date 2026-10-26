"""Turn a request for a static file into a response."""

import secrets
from pathlib import Path

from .conditional import Validators, evaluate
from .config import ServerConfig
from .dates import format_http_date
from .encoding import accepts_gzip, compress, compressible, gzip_etag
from .files import file_info, resolve
from .http import REASONS, FileBody, Headers, Request, Response
from .ranges import ByteRange, if_range_allows, parse_range, satisfiable


ALLOWED_METHODS = ('GET', 'HEAD')


def handle(request: Request, config: ServerConfig) -> Response:
    if request.method not in ALLOWED_METHODS:
        return error(405, Headers([('Allow', ', '.join(ALLOWED_METHODS))]))

    path = resolve(config.root, request.target)
    if path is None:
        return error(404)

    info = file_info(path)
    negotiated = compressible(info.content_type, info.size, config.min_compress_bytes)
    gzipped = negotiated and accepts_gzip(request.headers.get('Accept-Encoding'))
    etag = gzip_etag(info.etag) if gzipped else info.etag

    headers = Headers(
        [
            ('ETag', etag),
            ('Last-Modified', format_http_date(info.modified)),
            ('Cache-Control', 'no-cache'),
        ]
    )
    if negotiated:
        headers.add('Vary', 'Accept-Encoding')

    match evaluate(request.method, request.headers, Validators(etag, info.modified)):
        case 304:
            return Response(304, headers)
        case 412:
            return error(412)
        case _:
            pass

    headers.add('Accept-Ranges', 'bytes')
    encoded = compress(path.read_bytes()) if gzipped else None
    length = info.size if encoded is None else len(encoded)

    ranges = _requested_ranges(request, etag, info.modified, length)
    if ranges == []:
        return error(416, Headers([('Content-Range', f'bytes */{length}')]))
    if ranges is not None and len(ranges) > 1 and encoded is None:
        return _multipart(headers, path, info.content_type, ranges, length)

    headers.add('Content-Type', info.content_type)
    if encoded is not None:
        headers.add('Content-Encoding', 'gzip')

    if ranges is None or len(ranges) > 1:
        body = encoded if encoded is not None else FileBody(path, 0, length)
        return Response(200, headers, body)

    [selected] = ranges
    headers.add('Content-Range', selected.content_range(length))
    if encoded is not None:
        return Response(206, headers, encoded[selected.start : selected.stop])
    return Response(206, headers, FileBody(path, selected.start, selected.length))


def error(status: int, headers: Headers | None = None) -> Response:
    headers = headers or Headers()
    headers.add('Content-Type', 'text/plain; charset=utf-8')
    return Response(status, headers, f'{status} {REASONS[status]}\n'.encode())


def _requested_ranges(
    request: Request, etag: str, modified: int, length: int
) -> list[ByteRange] | None:
    """Return the ranges to send, an empty list for none, or `None` for all."""
    field = request.headers.get('Range')
    if request.method != 'GET' or field is None:
        return None
    if not if_range_allows(request.headers.get('If-Range'), etag, modified):
        return None

    specs = parse_range(field)
    return None if specs is None else satisfiable(specs, length)


def _multipart(
    headers: Headers,
    path: Path,
    content_type: str,
    ranges: list[ByteRange],
    length: int,
) -> Response:
    boundary = secrets.token_hex(16)
    headers.add('Content-Type', f'multipart/byteranges; boundary={boundary}')

    parts: list[bytes] = []
    with path.open('rb') as file:
        for selected in ranges:
            file.seek(selected.start)
            head = (
                f'--{boundary}\r\n'
                f'Content-Type: {content_type}\r\n'
                f'Content-Range: {selected.content_range(length)}\r\n\r\n'
            )
            parts.extend((head.encode('latin-1'), file.read(selected.length), b'\r\n'))
    parts.append(f'--{boundary}--\r\n'.encode('latin-1'))
    return Response(206, headers, b''.join(parts))
