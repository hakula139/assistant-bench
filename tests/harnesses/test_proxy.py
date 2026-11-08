import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from assistant_bench.catalog import Api
from assistant_bench.generation import load_generation
from assistant_bench.harnesses.accounting import RequestReceipt, TrialAccounting
from assistant_bench.harnesses.endpoint import (
    HEADERS_VAR,
    SECRET_HEADERS_FILE_VAR,
    Endpoint,
)
from assistant_bench.harnesses.proxy import connect
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.state import State
from support import StubEndpoint, message, message_stream, response


class TestConnect:
    @pytest.mark.parametrize(
        ('api', 'usage_case'),
        [
            ('anthropic-messages', 'measured'),
            ('anthropic-messages', 'missing'),
            ('openai-responses', 'measured'),
            ('openai-responses', 'missing'),
            ('anthropic-messages', 'null-output'),
        ],
        ids=[
            'messages-measured-null-inputs',
            'messages-usage-missing',
            'responses-measured',
            'responses-usage-missing',
            'messages-null-output',
        ],
    )
    def test_forwards_stream_bytes_and_records_only_completed_response_usage(
        self,
        endpoint: StubEndpoint,
        begun_trial: TrialAccounting,
        private_state: State,
        tmp_path: Path,
        api: Api,
        usage_case: str,
    ) -> None:
        payload = message() if api == 'anthropic-messages' else response()
        if usage_case == 'missing':
            payload['usage'] = None
        event = {'type': 'response.completed', 'response': payload}
        stream = (
            message_stream(payload)
            if api == 'anthropic-messages'
            else [f'data: {json.dumps(event)}\n\n'.encode()]
        )
        if api == 'anthropic-messages' and usage_case != 'missing':
            delta = json.loads(stream[-2].split(b'data: ', 1)[1])
            delta['usage'].update(
                {
                    'input_tokens': None,
                    'cache_read_input_tokens': None,
                    'cache_creation_input_tokens': None,
                }
                if usage_case == 'measured'
                else {'output_tokens': None}
            )
            stream[-2] = f'event: message_delta\ndata: {json.dumps(delta)}\n\n'.encode()
        endpoint.streams = [stream, stream[:-1]]
        secret_file = tmp_path / 'secret-headers.json'
        secret_file.write_text(
            json.dumps(
                {
                    'x-session-token': 'seed',
                    'Authorization': 'Bearer configured-but-never-sent',
                }
            )
        )
        environment = endpoint.env | {
            HEADERS_VAR: json.dumps(
                {
                    'x-trace': 'bench',
                    'x-client-label': 'twig',
                    'Content-Type': 'application/unused',
                    'anthropic-version': 'configured-version',
                    'anthropic-beta': 'configured-beta',
                }
            ),
            SECRET_HEADERS_FILE_VAR: str(secret_file),
        }
        configured = Endpoint.read(environment.get, api)
        path = '/v1/messages' if api == 'anthropic-messages' else '/v1/responses'
        body = b'{"model":"acme/large","stream":true,"input":"hello"}'

        async def request() -> tuple[bytes, list[dict[str, Any]]]:
            async with connect(
                configured, api, 'acme/large', recorder=begun_trial
            ) as connection:
                url = connection.url.replace('host.docker.internal', '127.0.0.1')
                async with httpx2.AsyncClient() as client:
                    headers = {'Authorization': f'Bearer {connection.token}'}
                    if api == 'anthropic-messages':
                        headers['anthropic-beta'] = 'requested-beta'
                    first = await client.post(url + path, content=body, headers=headers)
                    partial: list[bytes] = []
                    with pytest.raises(httpx2.RemoteProtocolError):
                        async with client.stream(
                            'POST', url + path, content=body, headers=headers
                        ) as reply:
                            async for chunk in reply.aiter_bytes():
                                partial.append(chunk)
                    assert b''.join(partial) == b''.join(stream[:-1])
                return first.content, connection.calls

        content, calls = asyncio.run(request())

        reported = usage_case == 'measured'
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
        (saved,) = [
            RequestReceipt.model_validate_json(row['receipt_json'])
            for row in private_state.requests('sample')
            if isinstance(row['receipt_json'], str)
        ]
        assert saved.outcome == 'completed'
        assert saved.usage == MeasuredUsage.from_details(calls[0])
        assert all(
            sent.headers['authorization'] == 'Bearer test-key'
            for sent in endpoint.requests
        )
        assert all(sent.headers['x-trace'] == 'bench' for sent in endpoint.requests)
        assert all(
            sent.headers['x-client-label'] == 'twig' for sent in endpoint.requests
        )
        assert all(
            sent.headers['x-session-token'] == 'seed' for sent in endpoint.requests
        )
        assert all(
            sent.headers['content-type'] == 'application/json'
            for sent in endpoint.requests
        )
        if api == 'anthropic-messages':
            assert all(
                sent.headers['anthropic-version']
                == load_generation().anthropic_api_version
                and sent.headers['anthropic-beta'] == 'requested-beta'
                for sent in endpoint.requests
            )
        remembered = begun_trial.secrets()
        assert 'seed' in remembered
        unused_credential_remembered = 'configured-but-never-sent' in remembered
        assert not unused_credential_remembered
        ordinary_metadata_remembered = 'twig' in remembered
        assert not ordinary_metadata_remembered
        assert all(sent.body['model'] == 'acme/large' for sent in endpoint.requests)
        assert begun_trial.fatal

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
        begun_trial: TrialAccounting,
        authorization: str | None,
        path: str,
        body: object,
        status: int,
    ) -> None:
        configured = Endpoint.read(endpoint.env.get, 'openai-responses')

        async def request() -> int:
            async with connect(
                configured, 'openai-responses', 'acme/large', recorder=begun_trial
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

    @pytest.mark.parametrize('api', ['anthropic-messages', 'openai-responses'])
    @pytest.mark.parametrize('event_stream', [False, True], ids=['json', 'sse'])
    def test_http_errors_are_forwarded_after_a_durable_unknown_usage_receipt(
        self,
        endpoint: StubEndpoint,
        begun_trial: TrialAccounting,
        private_state: State,
        api: Api,
        event_stream: bool,
    ) -> None:
        endpoint.status = 503
        endpoint.response = {'error': {'message': 'Unavailable.'}}
        if event_stream:
            endpoint.stream = [b'Unavailable.']
        configured = Endpoint.read(endpoint.env.get, api)
        path = '/v1/messages' if api == 'anthropic-messages' else '/v1/responses'

        async def request() -> None:
            async with connect(
                configured, api, 'acme/large', recorder=begun_trial
            ) as connection:
                url = connection.url.replace('host.docker.internal', '127.0.0.1')
                async with httpx2.AsyncClient() as client:
                    reply = await client.post(
                        url + path,
                        json={'model': 'acme/large', 'stream': True},
                        headers={'Authorization': f'Bearer {connection.token}'},
                    )
                    assert reply.status_code == 503
                    if event_stream:
                        assert reply.content == b'Unavailable.'
                    else:
                        assert reply.json() == endpoint.response
                    (stored,) = private_state.requests('sample')
                    assert isinstance(stored['receipt_json'], str)
                    saved = RequestReceipt.model_validate_json(stored['receipt_json'])
                    assert (
                        saved.api,
                        saved.outcome,
                        saved.http_status,
                        saved.usage,
                    ) == (api, 'http_error', 503, None)

        asyncio.run(request())

        assert len(endpoint.requests) == 1
        assert not begun_trial.fatal

    @pytest.mark.parametrize(
        'ending', [b'\n', b'\r\n', b'\r'], ids=['lf', 'crlf', 'cr']
    )
    @pytest.mark.parametrize('split', [1, 7, 4096], ids=['byte', 'small', 'whole'])
    @pytest.mark.parametrize('compressed', [False, True], ids=['plain', 'gzip'])
    @pytest.mark.parametrize(
        'content_type',
        ['text/event-stream', 'Text/Event-Stream; Charset=UTF-8'],
        ids=['lowercase-mime', 'mixed-case-mime'],
    )
    def test_preserves_complete_frame_bytes_with_a_durable_terminal_receipt(
        self,
        endpoint: StubEndpoint,
        begun_trial: TrialAccounting,
        private_state: State,
        ending: bytes,
        split: int,
        compressed: bool,
        content_type: str,
    ) -> None:
        payload = response('Hello, 世界.')
        event = {'type': 'response.completed', 'response': payload}
        raw = (
            b': keep-alive'
            + ending * 2
            + b'event: response.completed'
            + ending
            + b'data: '
            + json.dumps(event, ensure_ascii=False).encode()
            + ending * 2
        )
        endpoint.stream = [
            raw[index : index + split] for index in range(0, len(raw), split)
        ]
        endpoint.gzip = compressed
        endpoint.content_type = content_type
        configured = Endpoint.read(endpoint.env.get, 'openai-responses')

        async def request() -> bytes:
            async with connect(
                configured, 'openai-responses', 'acme/large', recorder=begun_trial
            ) as connection:
                url = connection.url.replace('host.docker.internal', '127.0.0.1')
                async with (
                    httpx2.AsyncClient() as client,
                    client.stream(
                        'POST',
                        url + '/v1/responses',
                        json={'model': 'acme/large', 'stream': True},
                        headers={'Authorization': f'Bearer {connection.token}'},
                    ) as reply,
                ):
                    received = bytearray()
                    async for chunk in reply.aiter_bytes():
                        received.extend(chunk)
                        if b'response.completed' in received:
                            (stored,) = private_state.requests('sample')
                            assert isinstance(stored['receipt_json'], str)
                    return bytes(received)

        received = asyncio.run(request())

        assert received == raw
        (stored,) = private_state.requests('sample')
        assert isinstance(stored['receipt_json'], str)
        saved = RequestReceipt.model_validate_json(stored['receipt_json'])
        assert saved.usage is not None
        assert saved.usage.output_tokens == 40
        assert not begun_trial.fatal

    def test_failed_terminal_persistence_never_forwards_terminal_bytes(
        self,
        endpoint: StubEndpoint,
        begun_trial: TrialAccounting,
        private_state: State,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prefix = b': ready\n\n'
        terminal = json.dumps(
            {'type': 'response.completed', 'response': response()}
        ).encode()
        endpoint.stream = [prefix, b'data: ' + terminal + b'\n\n']
        configured = Endpoint.read(endpoint.env.get, 'openai-responses')

        def unavailable(self: State, request_id: str, receipt_json: str) -> None:
            raise OSError('Database unavailable.')

        monkeypatch.setattr(State, 'record_receipt', unavailable)

        async def request() -> bytes:
            async with connect(
                configured, 'openai-responses', 'acme/large', recorder=begun_trial
            ) as connection:
                url = connection.url.replace('host.docker.internal', '127.0.0.1')
                received = bytearray()
                async with httpx2.AsyncClient() as client:
                    with pytest.raises(httpx2.RemoteProtocolError):
                        async with client.stream(
                            'POST',
                            url + '/v1/responses',
                            json={'model': 'acme/large', 'stream': True},
                            headers={'Authorization': f'Bearer {connection.token}'},
                        ) as reply:
                            async for chunk in reply.aiter_bytes():
                                received.extend(chunk)
                return bytes(received)

        received = asyncio.run(request())

        assert b'response.completed' not in received
        (stored,) = private_state.requests('sample')
        assert stored['receipt_json'] is None
        assert begun_trial.fatal
