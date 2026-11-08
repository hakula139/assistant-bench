import asyncio
import json
import os
import ssl
import traceback
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import httpx
import httpx2
import openai
import pytest
from pydantic import ValidationError

from assistant_bench import jobs
from assistant_bench.artifacts import ArtifactRef, audit_trial
from assistant_bench.catalog import EXPORT_VAR, Api
from assistant_bench.database import SQLiteDatabase
from assistant_bench.families import load_cases, load_family
from assistant_bench.harnesses.accounting import (
    AccountingError,
    RequestReceipt,
    TrialAccounting,
)
from assistant_bench.harnesses.endpoint import (
    ANTHROPIC_BASE_URL_VAR,
    BASE_URL_VAR,
    CA_FILE_VAR,
    HEADERS_VAR,
    KEY_FILE_VAR,
    LOCAL_ADDRESS_VAR,
    SDK_HEADERS_VAR,
    SECRET_HEADERS_FILE_VAR,
    Endpoint,
    HeaderSettings,
    preflight,
)
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.messages import InputError
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Entry
from assistant_bench.state import State
from assistant_bench.tasks import render_tasks
from support import (
    START,
    StubEndpoint,
    accounted_trial,
    completion,
    executed_trial,
    make_system,
    message,
    response,
)


pytestmark = pytest.mark.usefixtures('export')


async def request_native(
    client: openai.AsyncOpenAI | anthropic.AsyncAnthropic,
    api: Api,
    *,
    extra_headers: dict[str, str] | None = None,
) -> None:
    if isinstance(client, anthropic.AsyncAnthropic):
        await client.messages.create(
            model='acme/large',
            max_tokens=64,
            messages=[{'role': 'user', 'content': 'Hello.'}],
            extra_headers=extra_headers,
        )
    elif api == 'openai-responses':
        await client.responses.create(
            model='acme/large', input='Hello.', extra_headers=extra_headers
        )
    else:
        await client.chat.completions.create(
            model='acme/large',
            messages=[{'role': 'user', 'content': 'Hello.'}],
            extra_headers=extra_headers,
        )


class TestLoopbackRequestsOnly:
    @pytest.mark.parametrize(
        'client_type',
        [httpx.AsyncClient, httpx2.AsyncClient],
        ids=['httpx', 'httpx2'],
    )
    def test_blocks_non_loopback_requests_before_network_io(
        self, client_type: type[httpx.AsyncClient] | type[httpx2.AsyncClient]
    ) -> None:
        async def request() -> None:
            async with client_type() as client:
                await client.get('https://example.com/v1/responses')

        with pytest.raises(pytest.fail.Exception, match='non-loopback.*example.com'):
            asyncio.run(request())


class TestHeaderSettings:
    @pytest.mark.parametrize(
        ('ordinary', 'confidential'),
        [
            ('{"x-mode":"first","x-mode":"second"}', '{}'),
            ('{"X-Mode":"first","x-mode":"second"}', '{}'),
            ('{}', '{"x-session-token":"seed","x-session-token":"twig"}'),
            ('{}', '{"X-Session-Token":"seed","x-session-token":"twig"}'),
            ('{"X-Mode":"first"}', '{"x-mode":"second"}'),
            ('{"Authorization":"Bearer seed"}', '{}'),
            ('{"PROXY-AUTHORIZATION":"Bearer seed"}', '{}'),
            ('{"X-Api-Key":"seed"}', '{}'),
            ('{}', '{"x-private-field":"synthetic-header-marker\\r\\nvalue"}'),
            ('{}', '{"x-private field":"synthetic-header-marker"}'),
            ('{}', '{"x-private-field":"synthetic-header-marker\\u2603"}'),
            ('{}', '{"x-private-field":1}'),
            ('{}', '{"x-private-field":["synthetic-header-marker"]}'),
        ],
        ids=[
            'ordinary-exact-duplicate',
            'ordinary-case-duplicate',
            'secret-exact-duplicate',
            'secret-case-duplicate',
            'cross-map-duplicate',
            'ordinary-authorization',
            'ordinary-proxy-authorization',
            'ordinary-api-key',
            'secret-newline-value',
            'secret-invalid-name',
            'secret-unicode-value',
            'secret-numeric-value',
            'secret-array-value',
        ],
    )
    def test_rejects_invalid_headers_without_exposing_names_or_values(
        self, tmp_path: Path, ordinary: str, confidential: str
    ) -> None:
        path = tmp_path / 'secret-headers.json'
        path.write_text(confidential)
        env = {HEADERS_VAR: ordinary, SECRET_HEADERS_FILE_VAR: str(path)}

        with pytest.raises(ValueError) as failure:
            HeaderSettings.from_env(env.get).resolve()

        rendered = ''.join(traceback.format_exception(failure.value))
        assert all(
            value not in rendered
            for value in (
                'seed',
                'twig',
                'first',
                'second',
                'x-private',
                'synthetic-header-marker',
                '\\u2603',
                '\u2603',
            )
        )


class TestEndpoint:
    def test_request_limit_denies_the_third_post_without_inventing_its_usage(
        self, endpoint: StubEndpoint, private_state: State
    ) -> None:
        endpoint.status = 503
        endpoint.response = {'error': {'message': 'Unavailable.'}}
        configured = Endpoint.read(endpoint.env.get, 'openai-responses')

        with accounted_trial(
            private_state, scope='bounded', max_requests=2
        ) as recorder:
            recorder.begin(START)

            async def request() -> None:
                async with configured.http_client('openai-responses') as client:
                    for _ in range(2):
                        reply = await client.post(
                            f'{endpoint.url}/v1/responses', json={}
                        )
                        assert reply.status_code == 503
                    with pytest.raises(AccountingError, match='was not granted'):
                        await client.post(f'{endpoint.url}/v1/responses', json={})

            asyncio.run(request())
            recorder.finish(START)
            attempt = recorder.attempt()
            assert not recorder.fatal
            assert private_state.claim_state(recorder.claim_id) == 'claimed'

        assert [sent.path for sent in endpoint.requests] == ['/v1/responses'] * 2
        records = private_state.requests('bounded')
        assert len(records) == 2
        assert all(record['receipt_json'] is not None for record in records)
        assert attempt is not None
        assert attempt.responses == [None, None]

    def test_nonbillable_token_counting_needs_no_trial_admission(
        self,
        endpoint: StubEndpoint,
        private_state: State,
        private_store: SQLiteDatabase,
        tmp_path: Path,
    ) -> None:
        endpoint.response = {'input_tokens': 5}
        original_key = endpoint.key_file.read_text().strip()
        original_file = tmp_path / 'original-headers.json'
        original_file.write_text('{"x-session-token":"seed"}')
        environment = endpoint.env | {SECRET_HEADERS_FILE_VAR: str(original_file)}
        configured = Endpoint.read(environment.get, 'anthropic-messages')

        async def request() -> None:
            client = configured.messages_client()
            endpoint.key_file.write_text('rotated-endpoint-key')
            original_file.write_text('{"x-rewritten-token":"rewritten-secret"}')
            rotated_file = tmp_path / 'rotated-headers.json'
            rotated_file.write_text('{"x-rotated-token":"later-secret"}')
            environment[SECRET_HEADERS_FILE_VAR] = str(rotated_file)
            async with client:
                result = await client.messages.count_tokens(
                    model='acme/large',
                    messages=[{'role': 'user', 'content': 'Hello.'}],
                )
                assert result.input_tokens == 5

        with accounted_trial(private_state) as recorder:
            asyncio.run(request())

        assert [sent.path for sent in endpoint.requests] == [
            '/anthropic/v1/messages/count_tokens'
        ]
        assert private_state.requests('sample') == []
        assert private_store.execute('SELECT * FROM request_receipts').rows == []
        assert private_store.execute('SELECT * FROM attempt_starts').rows == []
        assert recorder.attempt() is None
        (sent,) = endpoint.requests
        assert sent.headers['authorization'] == f'Bearer {original_key}'
        assert sent.headers['x-session-token'] == 'seed'
        assert 'x-rewritten-token' not in sent.headers
        assert 'x-rotated-token' not in sent.headers
        remembered = all(
            value in recorder.secrets() for value in (original_key, 'seed')
        )
        assert remembered
        echo = tmp_path / 'echo'
        echo.mkdir()
        (echo / 'trace.txt').write_text('seed')

        with pytest.raises(InputError, match='known credential'):
            audit_trial(echo, set(recorder.secrets()))

    @pytest.mark.usefixtures('begun_trial')
    class TestHttpClient:
        @pytest.mark.parametrize(
            ('api', 'usage'),
            [
                ('openai-chat-completions', MeasuredUsage(120, 100, 0, 40, 25)),
                ('openai-responses', MeasuredUsage(120, 100, 0, 40, 25)),
                ('anthropic-messages', MeasuredUsage(130, 100, 10, 40, None)),
            ],
            ids=['chat-completions', 'responses', 'messages'],
        )
        @pytest.mark.parametrize(
            'credential_source', ['api-key', 'ordinary-header', 'secret-header']
        )
        def test_only_credentials_are_refused_in_artifacts_and_provider_metadata(
            self,
            endpoint: StubEndpoint,
            run_context: RunContext,
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
            api: Api,
            usage: MeasuredUsage,
            credential_source: str,
        ) -> None:
            confidential = credential_source != 'ordinary-header'
            marker = (
                endpoint.key_file.read_text().strip()
                if credential_source == 'api-key'
                else 'twig'
            )
            endpoint.response = {
                'openai-chat-completions': completion(),
                'openai-responses': response(),
                'anthropic-messages': message(),
            }[api] | {'id': marker, 'model': marker}
            endpoint.response_headers['x-request-id'] = marker
            environment = endpoint.env | {
                HEADERS_VAR: '{"x-trace": "synthetic-endpoint-trace"}'
            }
            if credential_source == 'ordinary-header':
                environment[HEADERS_VAR] = json.dumps({'x-client-label': marker})
            elif credential_source == 'secret-header':
                secret_file = tmp_path / 'secret-headers.json'
                secret_file.write_text(json.dumps({'x-client-label': marker}))
                environment[SECRET_HEADERS_FILE_VAR] = str(secret_file)
            for name, value in environment.items():
                monkeypatch.setenv(name, value)
            configured = Endpoint.read(environment.get, api)
            system = make_system(
                api=api,
                thinking_mode='anthropic-adaptive'
                if api == 'anthropic-messages'
                else None,
            )
            family = load_family('smoke-text')
            tasks = render_tasks(
                family,
                load_cases(family, private_root=run_context.private_root)[:1],
                root=run_context.root,
            )
            config = jobs.job(
                family, [system], tasks, root=run_context.root, sources=[]
            )
            config.n_attempts = 1

            async def request() -> None:
                async with executed_trial(
                    run_context, config, system, scope='metadata'
                ) as trial:
                    (trial.paths.agent_dir / 'trace.txt').write_text(
                        f'{marker}_count = 1\n'
                    )
                    trial.recorder.begin(datetime.now(UTC))
                    try:
                        client = (
                            configured.messages_client()
                            if api == 'anthropic-messages'
                            else configured.client(api)
                        )
                        async with client:
                            await request_native(client, api)
                    finally:
                        trial.recorder.finish(datetime.now(UTC))

            if confidential:
                with pytest.raises(InputError, match='known credential'):
                    asyncio.run(request())
            else:
                asyncio.run(request())

            (stored,) = run_context.state.requests('metadata')
            assert isinstance(stored['receipt_json'], str)
            (entry_json,) = run_context.state.entries('metadata')
            if confidential:
                credential_is_absent = all(
                    marker not in value
                    for value in (stored['receipt_json'], entry_json)
                )
                assert credential_is_absent
            saved = RequestReceipt.model_validate_json(stored['receipt_json'])
            assert saved.usage == usage
            assert saved.http_status == 200
            assert saved.outcome == 'completed'
            assert (
                saved.response_id,
                saved.response_model,
                saved.provider_request_id,
            ) == (
                (None, None, None)
                if confidential
                else (marker, marker, f'req-1, {marker}')
            )
            entry = Entry.model_validate_json(entry_json)
            assert entry.responses == [usage]
            artifacts = run_context.state.artifact_generation('metadata').artifacts
            if confidential:
                assert artifacts == ()
            else:
                (artifact,) = artifacts
                archive = run_context.artifacts.materialize(
                    ArtifactRef(
                        commit_sha=artifact.commit_sha,
                        path=artifact.path,
                        manifest_digest=artifact.manifest_digest,
                    ),
                    tmp_path / 'recovered-trace',
                )
                trace = archive.job / archive.manifest.trial_name / 'agent/trace.txt'
                assert trace.read_text() == f'{marker}_count = 1\n'

        @pytest.mark.parametrize('api', ['openai-responses', 'anthropic-messages'])
        def test_remembers_sent_credentials_after_key_and_header_rotation(
            self,
            endpoint: StubEndpoint,
            begun_trial: TrialAccounting,
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
            api: Api,
        ) -> None:
            original_key = endpoint.key_file.read_text().strip()
            rotated_key = 'rotated-endpoint-key'
            original_header = 'original-endpoint-header'
            rotated_header = 'rotated-endpoint-header'
            for name, value in endpoint.env.items():
                monkeypatch.setenv(name, value)
            original_file = tmp_path / 'original-headers.json'
            original_file.write_text(json.dumps({'x-session-token': original_header}))
            monkeypatch.setenv(SECRET_HEADERS_FILE_VAR, str(original_file))
            monkeypatch.setenv(HEADERS_VAR, '{"x-client-label": "first"}')
            configured = Endpoint.read(os.environ.get, api)
            first_client = (
                configured.messages_client()
                if api == 'anthropic-messages'
                else configured.client(api)
            )
            endpoint.response = message() if api == 'anthropic-messages' else response()
            endpoint.key_file.write_text(rotated_key)
            original_file.write_text(
                json.dumps({'x-mutated-token': 'configured-but-never-sent'})
            )
            rotated_file = tmp_path / 'rotated-headers.json'
            rotated_file.write_text(json.dumps({'x-rotated-token': rotated_header}))
            monkeypatch.setenv(SECRET_HEADERS_FILE_VAR, str(rotated_file))
            monkeypatch.setenv(HEADERS_VAR, '{"x-client-label": "later"}')

            async def request() -> None:
                async with first_client:
                    await request_native(first_client, api)
                remembered_original = all(
                    value in begun_trial.secrets()
                    for value in (original_key, original_header)
                )
                assert remembered_original

                rotated = Endpoint.read(os.environ.get, api)
                second_client = (
                    rotated.messages_client()
                    if api == 'anthropic-messages'
                    else rotated.client(api)
                )
                async with second_client:
                    await request_native(second_client, api)

            asyncio.run(request())

            first, second = endpoint.requests
            sent_original = first.headers['authorization'] == f'Bearer {original_key}'
            sent_rotated = second.headers['authorization'] == f'Bearer {rotated_key}'
            assert sent_original and sent_rotated
            assert first.headers['x-session-token'] == original_header
            assert second.headers['x-rotated-token'] == rotated_header
            assert first.headers['x-client-label'] == 'first'
            assert second.headers['x-client-label'] == 'later'
            assert 'x-mutated-token' not in first.headers
            assert 'x-session-token' not in second.headers
            remembered_both = all(
                value in begun_trial.secrets()
                for value in (
                    original_key,
                    rotated_key,
                    original_header,
                    rotated_header,
                )
            )
            assert remembered_both
            unused_credential_remembered = (
                'configured-but-never-sent' in begun_trial.secrets()
            )
            assert not unused_credential_remembered

        @pytest.mark.parametrize(
            ('api', 'name', 'value', 'expected'),
            [
                ('openai-responses', 'Authorization', 'bEaReR seed', 'seed'),
                ('openai-responses', 'Proxy-Authorization', 'Bearer seed', 'seed'),
                ('anthropic-messages', 'x-api-key', 'seed', 'seed'),
                ('openai-responses', 'X-Session-Token', 'seed', 'seed'),
            ],
            ids=['sdk-authorization', 'proxy-authorization', 'sdk-api-key', 'custom'],
        )
        def test_remembers_actual_wire_overrides_instead_of_unused_configuration(
            self,
            endpoint: StubEndpoint,
            begun_trial: TrialAccounting,
            tmp_path: Path,
            api: Api,
            name: str,
            value: str,
            expected: str,
        ) -> None:
            path = tmp_path / 'secret-headers.json'
            path.write_text(json.dumps({'x-session-token': 'configured-but-not-sent'}))
            configured = Endpoint.read(
                (endpoint.env | {SECRET_HEADERS_FILE_VAR: str(path)}).get, api
            )
            endpoint.response = message() if api == 'anthropic-messages' else response()

            async def request() -> None:
                client = (
                    configured.messages_client()
                    if api == 'anthropic-messages'
                    else configured.client(api)
                )
                async with client:
                    await request_native(client, api, extra_headers={name: value})

            asyncio.run(request())

            (sent,) = endpoint.requests
            assert sent.headers[name.lower()] == value
            sent_credential_remembered = expected in begun_trial.secrets()
            assert sent_credential_remembered
            if name.lower() == 'x-session-token':
                unused_credential_remembered = (
                    'configured-but-not-sent' in begun_trial.secrets()
                )
                assert not unused_credential_remembered

        def test_configured_header_copy_cannot_change_the_client_snapshot(
            self,
            endpoint: StubEndpoint,
            begun_trial: TrialAccounting,
            tmp_path: Path,
        ) -> None:
            path = tmp_path / 'secret-headers.json'
            path.write_text('{"x-session-token":"seed"}')
            configured = Endpoint.read(
                (endpoint.env | {SECRET_HEADERS_FILE_VAR: str(path)}).get,
                'openai-responses',
            )

            async def request() -> None:
                async with configured.http_client('openai-responses') as client:
                    headers = client.configured_headers
                    headers['x-session-token'] = 'mutated-copy'
                    path.write_text('{"x-rotated-token":"later"}')
                    result = await client.post(
                        f'{endpoint.url}/v1/responses',
                        json={},
                        headers=client.configured_headers,
                    )
                    assert result.status_code == 200

            asyncio.run(request())

            (sent,) = endpoint.requests
            assert sent.headers['x-session-token'] == 'seed'
            assert 'x-rotated-token' not in sent.headers
            sent_credential_remembered = 'seed' in begun_trial.secrets()
            assert sent_credential_remembered

        @pytest.mark.parametrize(
            'api', ['openai-chat-completions', 'anthropic-messages']
        )
        def test_sends_the_key_and_extra_headers_with_the_request_timeout(
            self,
            endpoint: StubEndpoint,
            model_requests: list[httpx2.Request],
            api: Api,
        ) -> None:
            configured = Endpoint.read(endpoint.env.get, api)

            async def request() -> httpx2.Response:
                if api == 'anthropic-messages':
                    async with configured.messages_client() as client:
                        return await client.post(
                            f'{endpoint.url}/v1/responses',
                            cast_to=httpx2.Response,
                            body={},
                        )
                async with configured.client(api) as client:
                    return await client.post(
                        f'{endpoint.url}/v1/responses', cast_to=httpx2.Response, body={}
                    )

            reply = asyncio.run(request())

            assert reply.status_code == 200
            (sent,) = endpoint.requests
            assert sent.path == '/v1/responses'
            assert sent.headers['authorization'] == 'Bearer test-key'
            assert sent.headers['x-trace'] == 'bench'
            (outgoing,) = model_requests
            assert outgoing.extensions['timeout'] == {
                'connect': 30.0,
                'read': None,
                'write': 600.0,
                'pool': 600.0,
            }

        @pytest.mark.parametrize(
            ('api', 'error_type'),
            [
                ('openai-chat-completions', openai.APIStatusError),
                ('anthropic-messages', anthropic.APIStatusError),
            ],
        )
        def test_does_not_follow_a_redirect_to_another_endpoint(
            self,
            endpoint: StubEndpoint,
            ipv6_endpoint: StubEndpoint,
            api: Api,
            error_type: type[Exception],
        ) -> None:
            configured = Endpoint.read(endpoint.env.get, api)
            endpoint.status = 307
            endpoint.response_headers['Location'] = f'{ipv6_endpoint.url}/v1/responses'

            async def request() -> None:
                if api == 'anthropic-messages':
                    async with configured.messages_client() as client:
                        await client.post(
                            f'{endpoint.url}/v1/responses',
                            cast_to=httpx2.Response,
                            body={},
                        )
                    return
                async with configured.client(api) as client:
                    await client.post(
                        f'{endpoint.url}/v1/responses',
                        cast_to=httpx2.Response,
                        body={},
                    )

            with pytest.raises(error_type):
                asyncio.run(request())

            assert len(endpoint.requests) == 1
            assert ipv6_endpoint.requests == []

        @pytest.mark.parametrize(
            ('fixture', 'address'),
            [('tls_endpoint', '127.0.0.1'), ('ipv6_endpoint', '::1')],
            ids=['trusted-ca-ipv4', 'ipv6-bind'],
        )
        def test_uses_the_configured_trust_and_local_address(
            self,
            request: pytest.FixtureRequest,
            private_state: State,
            fixture: str,
            address: str,
        ) -> None:
            endpoint: StubEndpoint = request.getfixturevalue(fixture)
            env = endpoint.env | {LOCAL_ADDRESS_VAR: address}
            if endpoint.ca_file:
                env[CA_FILE_VAR] = str(endpoint.ca_file)
            configured = Endpoint.read(env.get, 'openai-responses')

            async def send(endpoint_config: Endpoint) -> httpx2.Response:
                async with endpoint_config.http_client('openai-responses') as client:
                    return await client.post(f'{endpoint.url}/v1/responses', json={})

            if endpoint.ca_file:
                untrusted = Endpoint.read(endpoint.env.get, 'openai-responses')
                with accounted_trial(private_state, scope='untrusted') as recorder:
                    recorder.begin(START)
                    with pytest.raises(httpx2.ConnectError):
                        asyncio.run(send(untrusted))
                    recorder.finish(START)
            with accounted_trial(private_state, scope='trusted') as recorder:
                recorder.begin(START)
                reply = asyncio.run(send(configured))
                recorder.finish(START)

            assert reply.status_code == 200
            (sent,) = endpoint.requests
            assert sent.path == '/v1/responses'

        @pytest.mark.parametrize(
            ('api', 'inputs', 'writes'),
            [
                ('openai-chat-completions', 120, 0),
                ('openai-responses', 120, 0),
                ('anthropic-messages', 130, 10),
            ],
        )
        def test_each_native_sdk_returns_only_after_its_receipt_is_durable(
            self,
            endpoint: StubEndpoint,
            begun_trial: TrialAccounting,
            private_state: State,
            api: Api,
            inputs: int,
            writes: int,
        ) -> None:
            endpoint.response = {
                'openai-chat-completions': completion(),
                'openai-responses': response(),
                'anthropic-messages': message(),
            }[api]
            configured = Endpoint.read(endpoint.env.get, api)

            async def request() -> None:
                client = (
                    configured.messages_client()
                    if api == 'anthropic-messages'
                    else configured.client(api)
                )
                async with client:
                    await request_native(client, api)
                (stored,) = private_state.requests('sample')
                assert isinstance(stored['receipt_json'], str)
                saved = RequestReceipt.model_validate_json(stored['receipt_json'])
                assert saved.api == api
                assert saved.usage is not None
                assert saved.usage.input_tokens == inputs
                assert saved.usage.cache_write_tokens == writes
                assert saved.response_model == 'large-2026'
                assert saved.provider_request_id == 'req-1'
                assert 'Hello.' not in stored['receipt_json']
                assert 'test-key' not in stored['receipt_json']
                assert 'x-trace' not in stored['receipt_json']

            asyncio.run(request())

            assert len(endpoint.requests) == 1
            assert not begun_trial.fatal

        def test_a_refused_admission_does_not_reach_the_endpoint(
            self,
            endpoint: StubEndpoint,
            begun_trial: TrialAccounting,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            async def refused(self: TrialAccounting, api: Api) -> str:
                raise ValueError('Admission refused.')

            monkeypatch.setattr(TrialAccounting, 'admit', refused)
            configured = Endpoint.read(endpoint.env.get, 'openai-responses')

            async def request() -> None:
                async with configured.http_client('openai-responses') as client:
                    await client.post(f'{endpoint.url}/v1/responses', json={})

            with pytest.raises(ValueError, match='Admission refused'):
                asyncio.run(request())

            assert endpoint.requests == []

        @pytest.mark.parametrize(
            'api', ['openai-chat-completions', 'openai-responses', 'anthropic-messages']
        )
        @pytest.mark.parametrize('event_stream', [False, True], ids=['json', 'sse'])
        def test_http_errors_have_durable_unknown_usage_even_with_stream_headers(
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

            async def request() -> None:
                async with configured.http_client(api) as client:
                    reply = await client.post(f'{endpoint.url}/v1/responses', json={})
                    assert reply.status_code == 503
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

        def test_a_caught_receipt_failure_still_blocks_every_followup_request(
            self,
            endpoint: StubEndpoint,
            begun_trial: TrialAccounting,
            private_state: State,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            configured = Endpoint.read(endpoint.env.get, 'openai-responses')

            def unavailable(self: State, request_id: str, receipt_json: str) -> None:
                raise OSError('Database unavailable.')

            monkeypatch.setattr(State, 'record_receipt', unavailable)

            async def request() -> None:
                async with configured.http_client('openai-responses') as client:
                    with pytest.raises(AccountingError, match='receipt could not'):
                        await client.post(f'{endpoint.url}/v1/responses', json={})
                    with pytest.raises(AccountingError, match='cannot admit'):
                        await client.post(f'{endpoint.url}/v1/responses', json={})

            asyncio.run(request())

            assert len(endpoint.requests) == 1
            assert begun_trial.fatal
            (stored,) = private_state.requests('sample')
            assert stored['receipt_json'] is None


class TestPreflight:
    def test_rejects_sdk_ambient_headers_before_native_requests(
        self, endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            SDK_HEADERS_VAR,
            'x-ambient-header: ambient-value\nanthropic-beta: ambient-beta',
        )

        with pytest.raises(ValueError, match='ANTHROPIC_CUSTOM_HEADERS'):
            preflight(endpoint.env, 'anthropic-messages')

    def test_requires_the_exact_native_base_without_falling_back_to_openai(
        self, endpoint: StubEndpoint
    ) -> None:
        env = endpoint.env.copy()
        env.pop('BENCH_ANTHROPIC_BASE_URL')

        with pytest.raises(ValueError, match='^Set `BENCH_ANTHROPIC_BASE_URL`'):
            preflight(env, 'anthropic-messages')

        assert endpoint.requests == []

    def test_names_each_missing_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(EXPORT_VAR)

        with pytest.raises(ValueError) as error:
            preflight({}, 'openai-chat-completions')

        assert str(error.value) == (
            'Set `BENCH_API_BASE_URL`, `BENCH_API_KEY_FILE`, `BENCH_NIXOS_CONFIG`. '
            'Copy `.env.example` to `.env`, and run inside `nix develop`.'
        )

    @pytest.mark.parametrize(
        ('variable', 'api'),
        [
            (BASE_URL_VAR, 'openai-chat-completions'),
            (ANTHROPIC_BASE_URL_VAR, 'anthropic-messages'),
        ],
    )
    def test_reports_an_invalid_base_url_under_the_variable_that_set_it(
        self, endpoint: StubEndpoint, variable: str, api: Api
    ) -> None:
        with pytest.raises(ValidationError) as error:
            preflight(endpoint.env | {variable: 'not a url'}, api)

        assert [detail['loc'][0] for detail in error.value.errors()] == [variable]

    @pytest.mark.parametrize(
        ('variable', 'value'),
        [
            (BASE_URL_VAR, 'api.example.com/v1'),
            (KEY_FILE_VAR, 'missing-key'),
            (HEADERS_VAR, '{"x-trace": 1}'),
            (SECRET_HEADERS_FILE_VAR, 'missing-headers.json'),
            (CA_FILE_VAR, 'missing-ca.pem'),
            (LOCAL_ADDRESS_VAR, 'eth0'),
        ],
    )
    def test_rejects_an_unusable_endpoint_setting(
        self, endpoint: StubEndpoint, variable: str, value: str
    ) -> None:
        with pytest.raises(ValidationError) as error:
            preflight(endpoint.env | {variable: value}, 'openai-chat-completions')

        assert [e['loc'][0] for e in error.value.errors()] == [variable]
        assert value not in str(error.value)

    def test_rejects_a_ca_bundle_that_is_not_pem(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        ca_file = tmp_path / 'ca.pem'
        ca_file.write_text('not a certificate\n')

        with pytest.raises(ssl.SSLError):
            preflight(
                endpoint.env | {CA_FILE_VAR: str(ca_file)}, 'openai-chat-completions'
            )

    def test_treats_an_empty_optional_setting_as_unset(
        self, endpoint: StubEndpoint
    ) -> None:
        empty = dict.fromkeys(
            (CA_FILE_VAR, HEADERS_VAR, SECRET_HEADERS_FILE_VAR, LOCAL_ADDRESS_VAR), ''
        )

        preflight(endpoint.env | empty, 'openai-chat-completions')
