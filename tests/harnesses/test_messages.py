import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message, Usage

from assistant_bench.harnesses.endpoint import (
    ANTHROPIC_BASE_URL_VAR,
    HEADERS_VAR,
    Endpoint,
)
from assistant_bench.harnesses.messages import (
    MessageCompletionError,
    MessageLengthExceededError,
    MessageResult,
    MessageStreamError,
    request_messages,
)
from support import StubEndpoint, message, message_stream


def request(endpoint: StubEndpoint, result: MessageResult, artifact: Path) -> None:
    async def run() -> None:
        async with Endpoint.read(
            endpoint.env.get, 'anthropic-messages'
        ).messages_client() as client:
            await request_messages(
                client,
                result,
                model='acme/large',
                messages=[{'role': 'user', 'content': 'Greet me.'}],
                system='Be direct.',
                max_tokens=128000,
                effort='high',
                thinking_mode='anthropic-adaptive',
                artifact=artifact,
            )

    asyncio.run(run())


class TestMessageResult:
    class TestDetails:
        @pytest.mark.parametrize(
            ('usage', 'expected'),
            [
                (
                    {
                        'input_tokens': 20,
                        'cache_read_input_tokens': 100,
                        'cache_creation_input_tokens': 10,
                        'output_tokens': 40,
                    },
                    (130, 100, 10, 40, None),
                ),
                (
                    {
                        'input_tokens': 0,
                        'cache_read_input_tokens': 0,
                        'cache_creation_input_tokens': 0,
                        'output_tokens': 0,
                        'output_tokens_details': {'thinking_tokens': 0},
                    },
                    (0, 0, 0, 0, 0),
                ),
                (
                    {
                        'input_tokens': 20,
                        'output_tokens': 40,
                        'output_tokens_details': {'thinking_tokens': 25},
                    },
                    (20, 0, 0, 40, 25),
                ),
                ({}, (None, None, None, None, None)),
                ({'output_tokens': 40}, (None, None, None, None, None)),
            ],
            ids=[
                'cache-read-and-write',
                'reported-zero',
                'reported-thinking',
                'missing-usage-fields',
                'missing-input',
            ],
        )
        def test_preserves_measured_usage_without_estimating_thinking(
            self, usage: dict[str, Any], expected: tuple[int | None, ...]
        ) -> None:
            payload = message()
            parsed = Message.model_validate(payload).model_copy(
                update={'usage': Usage.model_construct(**usage)}
            )
            result = MessageResult(parsed, complete=True)

            details = result.details

            assert (
                tuple(
                    details.get(name)
                    for name in (
                        'input_tokens',
                        'cache_tokens',
                        'cache_write_tokens',
                        'output_tokens',
                        'reasoning_tokens',
                    )
                )
                == expected
            )
            assert details['native_usage'] == usage

    class TestCheck:
        @pytest.mark.parametrize(
            ('stop', 'tool', 'finish', 'error'),
            [
                ('end_turn', False, 'stop', None),
                ('max_tokens', False, 'length', MessageLengthExceededError),
                ('refusal', False, 'refusal', MessageCompletionError),
                ('pause_turn', False, 'pause_turn', MessageCompletionError),
                ('stop_sequence', False, 'stop_sequence', MessageCompletionError),
                ('tool_use', True, 'unexpected_content', MessageCompletionError),
                ('end_turn', True, 'unexpected_content', MessageCompletionError),
            ],
            ids=[
                'complete',
                'budget',
                'refusal',
                'pause',
                'stop-sequence',
                'tool',
                'tool-with-end-turn',
            ],
        )
        def test_keeps_terminal_outcomes_distinct_for_the_completion_gate(
            self,
            endpoint: StubEndpoint,
            tmp_path: Path,
            stop: str,
            tool: bool,
            finish: str,
            error: type[Exception] | None,
        ) -> None:
            payload = message(' leading ', stop_reason=stop)
            payload['content'].extend(
                [
                    {'type': 'redacted_thinking', 'data': 'opaque-private'},
                    {'type': 'text', 'text': '\nsecond\r\nlast '},
                ]
            )
            if tool:
                payload['content'].append(
                    {
                        'type': 'tool_use',
                        'id': 'tool-1',
                        'name': 'read_file',
                        'input': {'path': 'greeting.txt'},
                    }
                )
            endpoint.stream = message_stream(payload)
            result = MessageResult()
            artifact = tmp_path / 'message.json'

            request(endpoint, result, artifact)
            if error is None:
                result.check(complete=True)
            else:
                with pytest.raises(error):
                    result.check(complete=True)

            assert result.complete
            assert result.text == ' leading \nsecond\r\nlast '
            assert result.items == payload['content']
            assert result.details['finish_reason'] == finish
            assert result.details['native_stop_reason'] == stop
            assert (
                Message.model_validate_json(artifact.read_text()).content
                == Message.model_validate(payload).content
            )
            assert len(endpoint.requests) == 1


class TestRequestMessages:
    def test_http_failure_sends_one_attempt_and_leaves_usage_unknown(
        self,
        endpoint: StubEndpoint,
        tmp_path: Path,
        messages_clients: list[anthropic.AsyncAnthropic],
    ) -> None:
        endpoint.status = 503
        endpoint.response = {
            'type': 'error',
            'error': {'type': 'overloaded_error', 'message': 'Unavailable.'},
        }
        result = MessageResult()
        artifact = tmp_path / 'message.json'

        with pytest.raises(anthropic.InternalServerError):
            request(endpoint, result, artifact)

        assert len(endpoint.requests) == 1
        assert not result.complete
        assert result.details['native_usage'] is None
        assert 'input_tokens' not in result.details
        assert not artifact.exists()
        (client,) = messages_clients
        assert client.is_closed()

    @pytest.mark.parametrize('base_path', ['/anthropic', '/anthropic/v1'])
    def test_sends_the_native_contract_with_explicit_credentials_and_version(
        self,
        endpoint: StubEndpoint,
        messages_clients: list[anthropic.AsyncAnthropic],
        model_requests: list[httpx2.Request],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        base_path: str,
    ) -> None:
        endpoint.stream = message_stream(message())
        env = endpoint.env | {
            HEADERS_VAR: '{"x-trace":"bench","anthropic-version":"wrong-version"}',
            ANTHROPIC_BASE_URL_VAR: f'{endpoint.url}{base_path}',
        }
        monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://example.com/wrong')
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'ambient-key')
        monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'ambient-token')
        result = MessageResult()

        async def run() -> None:
            async with Endpoint.read(
                env.get, 'anthropic-messages'
            ).messages_client() as client:
                await request_messages(
                    client,
                    result,
                    model='acme/large',
                    messages=[{'role': 'user', 'content': 'Greet me.'}],
                    system='Be direct.',
                    max_tokens=128000,
                    effort='high',
                    thinking_mode='anthropic-adaptive',
                    artifact=tmp_path / 'message.json',
                )

        asyncio.run(run())

        (sent,) = endpoint.requests
        assert sent.path == f'{base_path}/v1/messages'
        assert sent.body == {
            'model': 'acme/large',
            'messages': [{'role': 'user', 'content': 'Greet me.'}],
            'system': 'Be direct.',
            'max_tokens': 128000,
            'thinking': {'type': 'adaptive'},
            'output_config': {'effort': 'high'},
            'stream': True,
        }
        assert sent.headers['authorization'] == 'Bearer test-key'
        assert sent.headers['anthropic-version'] == '2023-06-01'
        assert sent.headers['x-trace'] == 'bench'
        assert 'anthropic-beta' not in sent.headers
        assert 'x-ambient-header' not in sent.headers
        assert sent.headers['x-stainless-retry-count'] == '0'
        assert result.request_id == 'req-1'
        assert result.details['response_id'] == 'msg-1'
        assert result.details['response_model'] == 'large-2026'
        assert result.details['anthropic_sdk_version'] == '2.3.0'
        assert result.details['anthropic_api_version'] == '2023-06-01'
        (outgoing,) = model_requests
        assert outgoing.extensions['timeout'] == {
            'connect': 30.0,
            'read': None,
            'write': 600.0,
            'pool': 600.0,
        }
        (client,) = messages_clients
        assert client.max_retries == 0
        assert client.is_closed()

    @pytest.mark.parametrize(
        ('ending', 'error'),
        [
            (None, MessageStreamError),
            (
                b'event: error\ndata: {"type":"error","error":'
                b'{"type":"overloaded_error","message":"Unavailable."}}\n\n',
                anthropic.APIError,
            ),
            (
                b'event: content_block_delta\ndata: {broken\n\n',
                json.JSONDecodeError,
            ),
        ],
        ids=['clean-eof-before-stop', 'native-error', 'malformed-event'],
    )
    def test_raises_for_an_unfinished_stream_and_keeps_its_private_snapshot(
        self,
        endpoint: StubEndpoint,
        tmp_path: Path,
        ending: bytes | None,
        error: type[Exception],
        messages_clients: list[anthropic.AsyncAnthropic],
    ) -> None:
        payload = message()
        chunks = message_stream(payload)
        endpoint.stream = chunks[:-1] if ending is None else chunks[:4] + [ending]
        result = MessageResult()
        artifact = tmp_path / 'partial.json'

        with pytest.raises(error):
            request(endpoint, result, artifact)

        assert not result.complete
        assert result.details['finish_reason'] is None
        assert result.details['response_status'] == 'incomplete'
        assert result.details['input_tokens'] == 130
        assert result.details['request_id'] == 'req-1'
        assert json.loads(artifact.read_text())['content'][0] == payload['content'][0]
        (client,) = messages_clients
        assert client.is_closed()
        assert len(endpoint.requests) == 1

    @pytest.mark.parametrize(
        ('ending', 'error'),
        [
            (
                b'event: error\ndata: {"type":"error","error":'
                b'{"type":"overloaded_error","message":"Unavailable."}}\n\n',
                anthropic.APIError,
            ),
            (
                b'event: content_block_delta\ndata: {broken\n\n',
                json.JSONDecodeError,
            ),
        ],
        ids=['native-error', 'malformed-event'],
    )
    def test_a_failed_stream_exit_does_not_commit_an_observed_terminal_event(
        self,
        endpoint: StubEndpoint,
        tmp_path: Path,
        ending: bytes,
        error: type[Exception],
    ) -> None:
        payload = message()
        endpoint.stream = [*message_stream(payload), ending]
        result = MessageResult()
        artifact = tmp_path / 'failed.json'

        with pytest.raises(error):
            request(endpoint, result, artifact)

        assert not result.complete
        assert result.details['response_status'] == 'incomplete'
        assert result.details['input_tokens'] == 130
        assert result.details['native_stop_reason'] == 'end_turn'
        assert (
            Message.model_validate_json(artifact.read_text()).content
            == Message.model_validate(payload).content
        )
        assert len(endpoint.requests) == 1

    @pytest.mark.parametrize(
        'during_thinking', [False, True], ids=['before-headers', 'active-thinking']
    )
    def test_cancellation_closes_the_stream_and_preserves_only_measured_evidence(
        self,
        endpoint: StubEndpoint,
        tmp_path: Path,
        during_thinking: bool,
        messages_clients: list[anthropic.AsyncAnthropic],
    ) -> None:
        endpoint.stream = message_stream(message())
        gate = threading.Event()
        waiting = endpoint.response_waiting
        if during_thinking:
            endpoint.stream_gate = gate
            endpoint.stream_gate_after = 4
            waiting = endpoint.stream_waiting
        else:
            endpoint.response_gate = gate
        result = MessageResult()
        artifact = tmp_path / 'partial.json'

        async def run() -> None:
            async with Endpoint.read(
                endpoint.env.get, 'anthropic-messages'
            ).messages_client() as client:
                task = asyncio.create_task(
                    request_messages(
                        client,
                        result,
                        model='acme/large',
                        messages=[{'role': 'user', 'content': 'Greet me.'}],
                        system=None,
                        max_tokens=128000,
                        effort='high',
                        thinking_mode='anthropic-adaptive',
                        artifact=artifact,
                    )
                )
                try:
                    assert await asyncio.to_thread(waiting.wait, 5)
                    if during_thinking:
                        async with asyncio.timeout(5):
                            while (
                                not result.items
                                or result.items[0].get('signature') != 'signed-msg-1'
                            ):
                                await asyncio.sleep(0)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
                finally:
                    gate.set()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(run())

        assert not result.complete
        assert result.details['finish_reason'] is None
        assert result.details['reasoning_tokens'] is None
        assert artifact.exists() is during_thinking
        assert result.details.get('input_tokens') == (130 if during_thinking else None)
        assert result.details.get('output_tokens') == (0 if during_thinking else None)
        (client,) = messages_clients
        assert client.is_closed()
        assert len(endpoint.requests) == 1
