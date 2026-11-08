"""A trial-scoped inference proxy that keeps endpoint credentials on the host."""

import hmac
import secrets
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from anthropic._streaming import SSEDecoder
from pydantic import TypeAdapter, ValidationError

from assistant_bench.catalog import Api
from assistant_bench.generation import load_generation
from assistant_bench.harnesses.accounting import REQUEST_ID, TrialAccounting
from assistant_bench.harnesses.endpoint import Endpoint, receipt
from assistant_bench.harnesses.usage import measure


_REQUEST = TypeAdapter(dict[str, Any])


class _FrameDecoder(SSEDecoder):
    def frames(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        return self._aiter_chunks(chunks)


@dataclass(frozen=True)
class Connection:
    url: str
    token: str
    calls: list[dict[str, Any]] = field(default_factory=list)


@asynccontextmanager
async def connect(
    endpoint: Endpoint, api: Api, model: str, *, recorder: TrialAccounting
) -> AsyncGenerator[Connection]:
    """Expose only the selected model's inference paths until the trial finishes."""
    token = secrets.token_urlsafe(32)
    recorder.remember_secret(token)
    calls: list[dict[str, Any]] = []
    paths = (
        ('/v1/messages', '/v1/messages/count_tokens')
        if api == 'anthropic-messages'
        else ('/v1/responses',)
    )
    async with endpoint.http_client(api, recorder=recorder) as client:

        async def forward(request: web.Request) -> web.StreamResponse:
            provided = request.headers.get('x-api-key', '')
            authorization = request.headers.get('Authorization', '')
            if authorization.startswith('Bearer '):
                provided = authorization.removeprefix('Bearer ')
            if not hmac.compare_digest(provided, token):
                raise web.HTTPUnauthorized()
            queries = ('', 'beta=true') if api == 'anthropic-messages' else ('',)
            if request.query_string not in queries:
                raise web.HTTPBadRequest()

            body = await request.read()
            try:
                payload = _REQUEST.validate_json(body)
            except ValidationError:
                raise web.HTTPBadRequest() from None
            if payload.get('model') != model:
                raise web.HTTPForbidden()

            path = (
                request.path
                if api == 'anthropic-messages'
                else request.path.removeprefix('/v1')
            )
            headers = {
                **client.configured_headers,
                'authorization': f'Bearer {endpoint.api_key()}',
                'content-type': 'application/json',
            }
            if api == 'anthropic-messages':
                headers['anthropic-version'] = load_generation().anthropic_api_version
                if beta := request.headers.get('anthropic-beta'):
                    headers['anthropic-beta'] = beta

            async with client.stream(
                'POST',
                f'{str(endpoint.base_url).rstrip("/")}{path}',
                params=request.query,
                content=body,
                headers=headers,
            ) as upstream:
                response = web.StreamResponse(
                    status=upstream.status_code,
                    headers={'Content-Type': upstream.headers['content-type']},
                )
                await response.prepare(request)

                request_id = upstream.request.extensions.get(REQUEST_ID)
                terminal = False
                try:
                    if (
                        upstream.is_success
                        and upstream.headers.get('content-type', '')
                        .split(';', 1)[0]
                        .strip()
                        .lower()
                        == 'text/event-stream'
                    ):
                        usage: dict[str, Any] = {}
                        message: dict[str, Any] = {}
                        final_usage = False
                        decoder = _FrameDecoder()
                        async for frame in decoder.frames(upstream.aiter_bytes()):
                            for event in decoder.iter_bytes(iter([frame])):
                                if not event.data or event.data == '[DONE]':
                                    continue
                                data = _REQUEST.validate_python(event.json())
                                kind = data.get('type')
                                completed: dict[str, Any] | None = None
                                if kind == 'message_start':
                                    message = _REQUEST.validate_python(data['message'])
                                    usage = dict(message.get('usage') or {})
                                elif kind == 'message_delta':
                                    usage.update(data.get('usage', {}))
                                    message.update(data.get('delta', {}))
                                    final_usage = True
                                elif kind == 'message_stop':
                                    completed = {
                                        **message,
                                        'usage': usage if final_usage else None,
                                    }
                                elif kind in (
                                    'response.completed',
                                    'response.incomplete',
                                    'response.failed',
                                ):
                                    completed = _REQUEST.validate_python(
                                        data['response']
                                    )
                                if completed is not None:
                                    assert isinstance(request_id, str)
                                    await recorder.confirm(
                                        request_id,
                                        receipt(
                                            api,
                                            completed,
                                            upstream.status_code,
                                            upstream.headers.get('x-request-id'),
                                        ),
                                    )
                                    terminal = True
                                    calls.append(
                                        {
                                            **measure(api, completed.get('usage')),
                                            'responded': True,
                                            'response_model': completed.get('model'),
                                        }
                                    )
                            await response.write(frame)
                        if isinstance(request_id, str) and not terminal:
                            await recorder.uncertain()
                            raise ValueError(
                                'The inference stream has no terminal receipt.'
                            )
                    else:
                        if isinstance(request_id, str):
                            terminal = True
                            try:
                                payload = _REQUEST.validate_json(upstream.content)
                            except ValidationError:
                                payload = {}
                            calls.append(
                                {
                                    **measure(api, payload.get('usage')),
                                    'responded': True,
                                    'response_model': payload.get('model'),
                                }
                            )
                        async for chunk in upstream.aiter_bytes():
                            await response.write(chunk)
                    await response.write_eof()
                    return response
                except BaseException:
                    if isinstance(request_id, str) and not terminal:
                        await recorder.uncertain()
                    raise

        app = web.Application(client_max_size=0)
        for path in paths:
            app.router.add_post(path, forward)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        await runner.setup()
        try:
            await web.TCPSite(runner, '0.0.0.0', 0).start()
            port = runner.addresses[0][1]
            yield Connection(f'http://host.docker.internal:{port}', token, calls)
        finally:
            await runner.cleanup()
