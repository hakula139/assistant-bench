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
from assistant_bench.harnesses.endpoint import Endpoint
from assistant_bench.harnesses.usage import measure


_REQUEST = TypeAdapter(dict[str, Any])


@dataclass(frozen=True)
class Connection:
    url: str
    token: str
    calls: list[dict[str, Any]] = field(default_factory=list)


@asynccontextmanager
async def connect(
    endpoint: Endpoint, api: Api, model: str
) -> AsyncGenerator[Connection]:
    """Expose only the selected model's inference paths until the trial finishes."""
    token = secrets.token_urlsafe(32)
    calls: list[dict[str, Any]] = []
    paths = (
        ('/v1/messages', '/v1/messages/count_tokens')
        if api == 'anthropic-messages'
        else ('/v1/responses',)
    )
    async with endpoint.http_client() as client:

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
                **(endpoint.headers or {}),
                'Authorization': f'Bearer {endpoint.api_key()}',
                'Content-Type': 'application/json',
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

                async def chunks() -> AsyncIterator[bytes]:
                    async for chunk in upstream.aiter_bytes():
                        await response.write(chunk)
                        yield chunk

                if 'text/event-stream' in upstream.headers.get('content-type', ''):
                    usage: dict[str, Any] = {}
                    reported_model: str | None = None
                    async for event in SSEDecoder().aiter_bytes(chunks()):
                        if not event.data or event.data == '[DONE]':
                            continue
                        data = event.json()
                        kind = data.get('type')
                        if kind == 'message_start':
                            usage = data['message'].get('usage') or {}
                            reported_model = data['message'].get('model')
                        elif kind == 'message_delta':
                            usage.update(data.get('usage', {}))
                        elif kind == 'message_stop':
                            calls.append(
                                {
                                    **measure(api, usage),
                                    'responded': True,
                                    'response_model': reported_model,
                                }
                            )
                        elif kind in (
                            'response.completed',
                            'response.incomplete',
                            'response.failed',
                        ):
                            completed = data['response']
                            calls.append(
                                {
                                    **measure(api, completed.get('usage')),
                                    'responded': True,
                                    'response_model': completed.get('model'),
                                }
                            )
                else:
                    async for _ in chunks():
                        pass
                await response.write_eof()
                return response

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
