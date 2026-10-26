"""Canonical-origin request policy and bounded native request bodies."""

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import FastAPI
from pyodide.ffi import JsNull

from assistant_bench_arena.runtime_models import Profile
from assistant_bench_arena.worker.bindings import BodyRejected, read_body


if TYPE_CHECKING:
    from js import Request, Response
    from workers import Request as WorkerRequest


class RequestRejected(Exception):
    """The request does not meet the configured application's ingress contract."""


async def serve_http(
    app: FastAPI, request: WorkerRequest, profile: Profile, environment: object
) -> Response:
    """Enforce the body bound before the native SDK buffers its ASGI receive queue."""
    from workers import Request as NativeRequest, Response as NativeResponse
    from workers.asgi import fetch

    native = request.js_object

    try:
        if native.method not in {'GET', 'POST'}:
            raise RequestRejected

        validate_request(native, profile, native.method)

        if native.method == 'POST':
            request = NativeRequest(request, body=await request_body(native))
    except RequestRejected:
        response = NativeResponse(
            '{"error":"invalid_request"}',
            status=400,
            headers={'Content-Type': 'application/json'},
        ).js_object
    else:
        response = await fetch(app, request, environment)

    return response


def validate_request(request: Request, profile: Profile, method: str) -> str:
    url = urlsplit(request.url)

    if (
        len(request.url) > 4096
        or f'{url.scheme}://{url.netloc}' != profile.app_origin
        or url.username is not None
        or url.password is not None
        or request.method != method
        or len(url.path) > 2048
        or '%' in url.path
        or '\\' in url.path
        or '//' in url.path
        or url.fragment
    ):
        raise RequestRejected

    site = header(request, 'Sec-Fetch-Site')

    if method == 'POST':
        if (
            header(request, 'Origin') != profile.app_origin
            or site not in {None, 'same-origin'}
            or header(request, 'Content-Type')
            not in {'application/json', 'application/json; charset=utf-8'}
            or header(request, 'Content-Encoding') is not None
        ):
            raise RequestRejected
    elif url.path.startswith('/api/') and site not in {None, 'same-origin', 'none'}:
        raise RequestRejected

    return url.path


async def request_body(request: Request) -> bytes:
    length = header(request, 'Content-Length')

    if length is not None and (not length.isascii() or not length.isdecimal()):
        raise RequestRejected

    try:
        if length is not None and int(length) > 8192:
            raise RequestRejected
    except ValueError:
        raise RequestRejected from None

    try:
        return await read_body(request.body)
    except BodyRejected:
        raise RequestRejected from None


def header(request: Request, name: str) -> str | None:
    value = request.headers.get(name)
    return None if isinstance(value, JsNull) else value
