"""Turn a request for a static file into a response."""

from .conditional import Validators, evaluate
from .config import ServerConfig
from .dates import format_http_date
from .encoding import accepts_gzip, compress, compressible, gzip_etag
from .files import file_info, resolve
from .http import REASONS, FileBody, Headers, Request, Response


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

    headers.add('Content-Type', info.content_type)
    if gzipped:
        headers.add('Content-Encoding', 'gzip')
        return Response(200, headers, compress(path.read_bytes()))

    return Response(200, headers, FileBody(path, 0, info.size))


def error(status: int, headers: Headers | None = None) -> Response:
    headers = headers or Headers()
    headers.add('Content-Type', 'text/plain; charset=utf-8')
    return Response(status, headers, f'{status} {REASONS[status]}\n'.encode())
