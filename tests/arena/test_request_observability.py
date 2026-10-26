import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import JsonValue, SecretBytes
from rich.text import Text

from assistant_bench_arena.admission import TokenCodec
from assistant_bench_arena.ledger_models import Moderation, Statement, Target
from assistant_bench_arena.policy import configuration
from assistant_bench_arena.runtime_models import KeyRing, KeyVersion, RenderClaims
from assistant_bench_arena.voting import RENDER_MS
from native_runtime_support import NAMESPACE, NativeRuntime


class TestDefaultFetch:
    def test_logs_only_safe_failure_context_and_keeps_expected_rejections_quiet(
        self, tmp_path: Path, subtests: pytest.Subtests
    ) -> None:
        canary = f'synthetic-secret-canary-{uuid4()}'
        responses: list[str] = []
        capabilities: list[str] = []
        expected_events: dict[str, list[str]] = {'owner.log': [], 'renderer.log': []}

        async def scenario(runtime: NativeRuntime) -> None:
            async with runtime.client() as (_, transport):
                inspected = await transport.call({'operation': 'inspect'})
                assert inspected['actual_app'] is True
                assert inspected['direct_entrypoint'] is (runtime.coverage is None)
                await transport.bootstrap(configuration(NAMESPACE))
                failures = (
                    ('preflight-runtime', 'preflight', 'runtime', 'RuntimeError'),
                    ('http-validation', 'http', 'validation', 'ValidationError'),
                    ('http-runtime', 'http', 'runtime', 'RuntimeError'),
                    ('management-runtime', 'management', 'runtime', 'RuntimeError'),
                )

                for name, stage, kind, exception in failures:
                    expected_events['owner.log'].append(event(stage, exception))
                    with subtests.test(name=name):
                        assert await transport.call(
                            {
                                'operation': 'request_failure',
                                'stage': stage,
                                'exception': kind,
                                'message': canary,
                            }
                        ) == {'configured': True}, name

                        try:
                            if stage == 'management':
                                result = await transport.call(
                                    {
                                        'operation': 'management',
                                        'request': {'operation': 'status'},
                                    }
                                )
                                assert result == {'error': {'reason': 'unavailable'}}, (
                                    name
                                )
                                responses.append(json.dumps(result))
                            else:
                                response = await transport.http('/api/status')
                                assert response['status'] == 503, (name, response)

                                assert isinstance(response['body'], str), name
                                assert json.loads(response['body']) == {
                                    'error': 'unavailable'
                                }, name
                                responses.append(json.dumps(response))
                                safe_headers(name, response)
                        finally:
                            assert await transport.call(
                                {
                                    'operation': 'request_failure',
                                    'stage': None,
                                    'exception': 'runtime',
                                    'message': '',
                                }
                            ) == {'configured': True}, name

                quiet: tuple[
                    tuple[str, str, dict[str, JsonValue] | None, int, dict[str, str]],
                    ...,
                ] = (
                    (
                        'status',
                        '/api/status',
                        None,
                        200,
                        {'status': 'admission_required'},
                    ),
                    (
                        'external-dto',
                        '/api/sessions',
                        {'command_id': canary, 'family': 'writing'},
                        400,
                        {'error': 'invalid_request'},
                    ),
                )

                for name, path, payload, status, expected in quiet:
                    with subtests.test(name=name):
                        response = await transport.http(path, body=payload)
                        assert response['status'] == status, (name, response)

                        assert isinstance(response['body'], str), name
                        assert json.loads(response['body']) == expected, (
                            name,
                            response,
                        )
                        responses.append(json.dumps(response))
                        safe_headers(name, response)

                now = time.time_ns() // 1_000_000
                keys = KeyRing(
                    versions=(
                        KeyVersion(
                            version='test', starts_at=0, key=SecretBytes(b'k' * 32)
                        ),
                    )
                )
                codec = TokenCodec(RenderClaims, keys, NAMESPACE, 'render', RENDER_MS)
                for issued_at, expires_at in (
                    (now, now + RENDER_MS),
                    (now - 2 * RENDER_MS, now - RENDER_MS),
                ):
                    capabilities.append(
                        codec.encode(
                            RenderClaims(
                                namespace=NAMESPACE,
                                issued_at=issued_at,
                                expires_at=expires_at,
                                lease=canary,
                                side='a',
                            )
                        )
                    )
                valid, expired = capabilities

                for name, capability in (
                    ('invalid-capability', canary),
                    ('expired-capability', expired),
                    ('absent-record', valid),
                ):
                    with subtests.test(name=name):
                        response = await transport.http(
                            f'/render/{capability}', renderer=True
                        )
                        assert response['status'] == 404, (name, response)

                        assert response['body'] == '', (name, response)
                        responses.append(json.dumps(response))
                        safe_headers(name, response)

                for name, serialized, exception in (
                    ('malformed-service-result', '{' + canary, 'ValidationError'),
                    (
                        'oversized-service-result',
                        canary + 'x' * 1_048_577,
                        'ValueError',
                    ),
                    (
                        'non-html-service-result',
                        json.dumps({'result': {'render': 'text', 'content': canary}}),
                        'ValueError',
                    ),
                ):
                    expected_events['renderer.log'].append(event('render', exception))
                    with subtests.test(name=name):
                        assert await transport.call(
                            {'operation': 'lookup_result', 'result': serialized}
                        ) == {'configured': True}, name

                        try:
                            response = await transport.http(
                                f'/render/{valid}', renderer=True
                            )
                            assert response['status'] == 503, (name, response)

                            assert response['body'] == '', (name, response)
                            responses.append(json.dumps(response))
                            safe_headers(name, response)
                        finally:
                            assert await transport.call(
                                {'operation': 'lookup_result', 'result': None}
                            ) == {'configured': True}, name

                expected_events['owner.log'].append(
                    event('render_lookup', 'JsException')
                )
                with subtests.test(name='lookup-database'):
                    view = await transport.rows(
                        "SELECT sql FROM sqlite_master WHERE type = 'view' "
                        "AND name = 'compatible_leases'"
                    )
                    original_sql = view[0]['sql']
                    assert isinstance(original_sql, str)
                    await transport.execute(
                        Statement(sql='DROP VIEW compatible_leases')
                    )

                    try:
                        response = await transport.http(
                            f'/render/{valid}', renderer=True
                        )
                        assert response['status'] == 503, ('lookup-database', response)

                        assert response['body'] == '', ('lookup-database', response)
                        responses.append(json.dumps(response))
                        safe_headers('lookup-database', response)
                    finally:
                        await transport.execute(Statement(sql=original_sql))

                assert await transport.call(
                    {
                        'operation': 'ingress_result',
                        'result': {
                            'success': False,
                            'metadata': {'source': 'synthetic'},
                        },
                    }
                ) == {'configured': True}

                rejected = await transport.http('/api/status')
                assert rejected['status'] == 429

                assert isinstance(rejected['body'], str)
                assert json.loads(rejected['body']) == {'error': 'rate_limited'}
                responses.append(json.dumps(rejected))
                safe_headers('rate-limited', rejected)
                assert runtime.provider.calls == []

        with NativeRuntime(tmp_path).owner() as runtime:
            asyncio.run(scenario(runtime))

        logs = {
            path.relative_to(runtime.directory).as_posix(): path.read_text()
            for path in runtime.directory.rglob('*.log')
        }
        prefix = 'Voting request failed:'
        for name, contents in logs.items():
            if '/' in name:
                continue

            events = [
                line[line.index(prefix) :]
                for line in Text.from_ansi(contents).plain.splitlines()
                if prefix in line
            ]

            with subtests.test(log=name):
                assert events == expected_events.get(name, []), (name, events)
        with subtests.test(name='private-values'):
            for secret in (canary, *capabilities):
                assert all(secret not in body for body in responses)
                assert all(secret not in contents for contents in logs.values())


class TestManagementRejection:
    def test_surfaces_and_logs_the_owner_input_diagnosis(self, tmp_path: Path) -> None:
        detail = 'A moderation plan must name its management principal.'

        async def scenario(runtime: NativeRuntime) -> None:
            async with runtime.client() as (_, transport):
                await transport.bootstrap(configuration(NAMESPACE))
                batch = Moderation(
                    id='reviewed-plan',
                    family='writing',
                    principal='owner',
                    action='exclude',
                    reason='Synthetic reviewed correction.',
                    created_at=0,
                    targets=(Target(receipt_seq=1, claim_version=0),),
                )

                result = await transport.call(
                    {
                        'operation': 'management',
                        'request': {
                            'operation': 'stage_moderation',
                            'batch': batch.model_dump(mode='json'),
                        },
                    }
                )

                assert result == {
                    'error': {'reason': 'invalid_request', 'detail': detail}
                }

        with NativeRuntime(tmp_path).owner() as runtime:
            asyncio.run(scenario(runtime))

        logs = [
            Text.from_ansi(path.read_text()).plain
            for path in runtime.directory.rglob('*.log')
            if '/' not in path.relative_to(runtime.directory).as_posix()
        ]
        assert any(
            f'Voting management rejected: exception=ValueError message={detail}'
            in contents
            for contents in logs
        )


def event(stage: str, exception: str) -> str:
    return f'Voting request failed: stage={stage} exception={exception}.'


def safe_headers(name: str, response: dict[str, JsonValue]) -> None:
    headers = response['headers']
    assert isinstance(headers, dict), (name, response)
    for header, expected in (
        ('cache-control', 'no-store'),
        ('referrer-policy', 'no-referrer'),
        ('x-content-type-options', 'nosniff'),
    ):
        assert headers.get(header) == expected, (name, response)
