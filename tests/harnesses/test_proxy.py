import asyncio
import json
from typing import Any

import httpx2
import pytest

from assistant_bench.catalog import Api
from assistant_bench.harnesses.endpoint import Endpoint
from assistant_bench.harnesses.proxy import connect
from support import StubEndpoint, message, message_stream, response


class TestConnect:
    @pytest.mark.parametrize('api', ['anthropic-messages', 'openai-responses'])
    @pytest.mark.parametrize(
        'reported', [True, False], ids=['measured', 'usage-missing']
    )
    def test_forwards_stream_bytes_and_records_only_completed_response_usage(
        self, endpoint: StubEndpoint, api: Api, reported: bool
    ) -> None:
        payload = message() if api == 'anthropic-messages' else response()
        if not reported:
            payload['usage'] = None
        event = {'type': 'response.completed', 'response': payload}
        stream = (
            message_stream(payload)
            if api == 'anthropic-messages'
            else [f'data: {json.dumps(event)}\n\n'.encode()]
        )
        endpoint.streams = [stream, stream[:-1]]
        configured = Endpoint.read(endpoint.env.get, api)
        path = '/v1/messages' if api == 'anthropic-messages' else '/v1/responses'
        body = b'{"model":"acme/large","stream":true,"input":"hello"}'

        async def request() -> tuple[bytes, list[dict[str, Any]]]:
            async with connect(configured, api, 'acme/large') as connection:
                url = connection.url.replace('host.docker.internal', '127.0.0.1')
                async with httpx2.AsyncClient() as client:
                    headers = {'Authorization': f'Bearer {connection.token}'}
                    first = await client.post(url + path, content=body, headers=headers)
                    partial = await client.post(
                        url + path, content=body, headers=headers
                    )
                    assert partial.content == b''.join(stream[:-1])
                return first.content, connection.calls

        content, calls = asyncio.run(request())

        assert content == b''.join(stream)
        assert len(calls) == 1
        assert calls[0]['response_model'] == 'large-2026'
        assert calls[0].get('input_tokens') == (
            (130 if api == 'anthropic-messages' else 120) if reported else None
        )
        assert calls[0].get('cache_write_tokens') == (
            (10 if api == 'anthropic-messages' else 0) if reported else None
        )
        assert calls[0].get('output_tokens') == (40 if reported else None)
        assert all(
            sent.headers['authorization'] == 'Bearer test-key'
            for sent in endpoint.requests
        )
        assert all(sent.headers['x-trace'] == 'bench' for sent in endpoint.requests)
        assert all(sent.body['model'] == 'acme/large' for sent in endpoint.requests)

    @pytest.mark.parametrize(
        ('authorization', 'path', 'body', 'status'),
        [
            ('Bearer wrong', '/v1/responses', {'model': 'acme/large'}, 401),
            (None, '/v1/responses', {'model': 'another'}, 403),
            (None, '/v1/responses', 'invalid', 400),
            (None, '/admin', {'model': 'acme/large'}, 404),
            (None, '/v1/messages', {'model': 'acme/large'}, 404),
            (None, '/v1/responses?other=true', {'model': 'acme/large'}, 400),
        ],
        ids=[
            'wrong-token',
            'wrong-model',
            'invalid-json',
            'unrelated-path',
            'wrong-api',
            'unregistered-query',
        ],
    )
    def test_rejects_requests_outside_the_trial_model_and_api(
        self,
        endpoint: StubEndpoint,
        authorization: str | None,
        path: str,
        body: object,
        status: int,
    ) -> None:
        configured = Endpoint.read(endpoint.env.get, 'openai-responses')

        async def request() -> int:
            async with connect(
                configured, 'openai-responses', 'acme/large'
            ) as connection:
                url = connection.url.replace('host.docker.internal', '127.0.0.1')
                async with httpx2.AsyncClient() as client:
                    reply = await client.post(
                        url + path,
                        content=body if isinstance(body, str) else json.dumps(body),
                        headers={
                            'Authorization': authorization
                            or f'Bearer {connection.token}'
                        },
                    )
                    return reply.status_code

        received = asyncio.run(request())

        assert received == status
        assert endpoint.requests == []
