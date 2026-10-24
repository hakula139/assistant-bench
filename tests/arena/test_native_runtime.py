import asyncio
import json
import math
import re
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from pydantic import JsonValue, TypeAdapter

from assistant_bench_arena.ledger_models import Point, Statement
from assistant_bench_arena.policy import configuration
from assistant_bench_arena.releases import Release, point_snapshot, publication_identity
from assistant_bench_arena.serialization import digest
from ledger_fixtures import make_release
from ledger_support import publish_release
from native_runtime_support import NAMESPACE, ROOT, NativeRuntime


def body(response: dict[str, JsonValue]) -> dict[str, JsonValue]:
    content = response['body']
    assert isinstance(content, str)
    return TypeAdapter(dict[str, JsonValue]).validate_json(content)


def cookie(response: dict[str, JsonValue]) -> str:
    headers = response['headers']
    assert isinstance(headers, dict)
    value = headers['set-cookie']
    assert isinstance(value, str)
    assert 'Secure' in value and 'HttpOnly' in value and 'SameSite=Strict' in value
    return value.split(';', 1)[0]


def admission(response: dict[str, JsonValue]) -> str:
    content = response['body']
    assert isinstance(content, str)
    match = re.search(
        r'<script id="admission-data" type="application/json">(.*?)</script>', content
    )
    assert match is not None
    value: dict[str, str] = json.loads(match[1])
    return value['command_id']


def html_release() -> Release:
    release = make_release(cases=1)
    presentation = release.publication.presentation.model_copy(
        update={'render': 'html'}
    )
    rendered = tuple(
        item.model_copy(update={'render': 'html'}) for item in release.rendered
    )
    approval = release.publication.approval
    assert approval is not None
    approval = approval.model_copy(
        update={
            'outputs': tuple(
                item.model_copy(
                    update={
                        'render_digest': digest(
                            {'render': 'html', 'content': output.content}
                        )
                    }
                )
                for item, output in zip(approval.outputs, rendered, strict=True)
            )
        }
    )
    publication = release.publication.model_copy(
        update={
            'id': publication_identity(
                release.evaluation.id, presentation, approval, rendered
            ),
            'presentation': presentation,
            'approval': approval,
        }
    )
    return release.model_copy(update={'publication': publication, 'rendered': rendered})


class TestNativeRuntime:
    def test_native_lock_uses_public_integrity_checked_wheels(self) -> None:
        lock = tomllib.loads((ROOT / 'arena' / 'pylock.toml').read_text())

        for package in lock['packages']:
            assert package['wheels'], package['name']
            for wheel in package['wheels']:
                location = urlsplit(str(wheel['url']))

                assert location.scheme == 'https', package['name']
                assert location.hostname == 'files.pythonhosted.org', package['name']
                assert re.fullmatch(r'[0-9a-f]{64}', wheel['hashes']['sha256'])

    def test_composes_real_entrypoints_and_commits_browser_commands_once(
        self, tmp_path: Path
    ) -> None:
        async def scenario(runtime: NativeRuntime) -> None:
            async with runtime.client() as (ledger, transport):
                inspected = await transport.call({'operation': 'inspect'})
                assert inspected == {
                    'heavy_import': False,
                    'actual_app': True,
                    'actual_renderer': 'assistant_bench_arena.rendering',
                }
                points = point_snapshot(
                    'e' * 64,
                    'p' * 64,
                    {
                        name: Point(score=score, component='connected')
                        for name, score in (
                            ('large', -1e308),
                            ('medium', 0.0),
                            ('small', 1e308),
                        )
                    },
                )
                for scale in (1.0, 100.0, math.ulp(0.0)):
                    weighted = await transport.call(
                        {
                            'operation': 'pair_weights',
                            'snapshot': points.model_dump(mode='json'),
                            'pairs': [
                                ['large', 'medium'],
                                ['medium', 'small'],
                                ['large', 'small'],
                            ],
                            'coverage': [
                                {'a_system': a, 'b_system': b, 'count': count}
                                for a, b, count in (
                                    ('large', 'medium', 2),
                                    ('medium', 'large', 2),
                                    ('medium', 'small', 9),
                                    ('small', 'medium', 9),
                                    ('large', 'small', 2),
                                    ('small', 'large', 2),
                                )
                            ],
                            'scale': scale,
                        }
                    )

                    assert weighted['weights'] == pytest.approx([1 / 5, 1 / 19, 0.0])

                page = await transport.http('/')
                assert page['status'] == 200
                assert 'bench-render-origin' in str(page['body'])
                page_headers = page['headers']
                assert isinstance(page_headers, dict)
                assert 'https://render.example.com' in str(
                    page_headers['content-security-policy']
                )
                assert page_headers['cache-control'] == 'no-store'

                families = await transport.http('/api/families')
                assert families['status'] == 200
                assert json.loads(str(families['body'])) == [
                    {
                        'name': 'writing',
                        'display_name': 'Synthetic writing',
                        'aspect': None,
                        'defects': ['tone'],
                    }
                ]
                invalid = await transport.http(
                    '/api/status', cookie='__Host-bench-credential=invalid'
                )
                assert invalid['status'] == 200
                assert body(invalid) == {'status': 'admission_required'}
                invalid_commands: tuple[JsonValue, ...] = (
                    'malformed',
                    True,
                    1,
                    [],
                    {},
                )
                for command in invalid_commands:
                    rejected = await transport.http(
                        '/api/sessions',
                        body={'command_id': command, 'family': 'writing'},
                    )

                    assert rejected['status'] == 400, repr(command)
                    assert body(rejected) == {'error': 'invalid_request'}

                wrong_family = await transport.http(
                    '/api/sessions',
                    body={'command_id': str(uuid4()), 'family': True},
                )
                assert wrong_family['status'] == 400
                assert body(wrong_family) == {'error': 'invalid_request'}
                unauthorized = await transport.http(
                    '/api/sessions',
                    body={'command_id': str(uuid4()), 'family': 'writing'},
                    cookie='__Host-bench-credential=invalid',
                )
                assert unauthorized['status'] == 401, body(unauthorized)
                assert body(unauthorized) == {'error': 'admission_required'}
                denied_render = await transport.http('/render/invalid', renderer=True)
                assert denied_render['status'] == 404
                assert denied_render['body'] == ''

                await transport.bootstrap(configuration(NAMESPACE))
                assert await transport.call({'operation': 'configure_provider'}) == {
                    'configured': True
                }
                status = await transport.call(
                    {'operation': 'management', 'request': {'operation': 'status'}}
                )
                assert isinstance(status['result'], dict)
                assert status['result']['namespace'] == NAMESPACE

                document = await transport.http('/admit?family=writing')
                assert document['status'] == 200
                flow_cookie = cookie(document)
                command = admission(document)
                request: dict[str, JsonValue] = {
                    'command_id': command,
                    'proof': 'admit-proof',
                }
                await transport.execute(
                    Statement(
                        sql="""
                            CREATE TRIGGER reject_test_credential
                            BEFORE INSERT ON credentials BEGIN
                                SELECT RAISE(ABORT, 'injected_failure');
                            END
                        """
                    )
                )

                failed = await transport.http(
                    '/api/admission', body=request, cookie=flow_cookie
                )
                assert failed['status'] == 503
                assert body(failed) == {'error': 'unavailable'}
                for table in (
                    'actors',
                    'credentials',
                    'admission_results',
                    'quota_windows',
                ):
                    assert await transport.rows(
                        f'SELECT count(*) AS count FROM {table}'
                    ) == [{'count': 0}], table

                await transport.execute(
                    Statement(sql='DROP TRIGGER reject_test_credential')
                )
                admitted = await transport.http(
                    '/api/admission', body=request, cookie=flow_cookie
                )
                assert admitted['status'] == 200
                assert body(admitted) == {'status': 'ready'}
                credential_cookie = cookie(admitted)
                replayed = await transport.http(
                    '/api/admission', body=request, cookie=flow_cookie
                )
                assert replayed['status'] == 200
                assert cookie(replayed) == credential_cookie
                assert runtime.provider.calls == [command, command]
                assert await transport.rows('SELECT count(*) AS count FROM actors') == [
                    {'count': 1}
                ]
                assert await transport.rows(
                    'SELECT count(*) AS count FROM admission_results'
                ) == [{'count': 1}]
                assert await transport.rows(
                    'SELECT scope, consumed FROM quota_windows '
                    "WHERE operation = 'admit' ORDER BY scope"
                ) == [
                    {'scope': 'deployment', 'consumed': 1},
                    {'scope': 'exact', 'consumed': 1},
                    {'scope': 'prefix', 'consumed': 1},
                ]

                renewal = await transport.http('/admit', cookie=credential_cookie)
                renew_command = admission(renewal)
                renewed = await transport.http(
                    '/api/admission',
                    body={'command_id': renew_command, 'proof': 'renew-proof'},
                    cookie=f'{credential_cookie}; {cookie(renewal)}',
                )
                assert renewed['status'] == 200
                renewed_cookie = cookie(renewed)
                assert renewed_cookie != credential_cookie
                old_status = await transport.http(
                    '/api/status', cookie=credential_cookie
                )
                assert body(old_status) == {'status': 'admission_required'}
                assert await transport.rows('SELECT count(*) AS count FROM actors') == [
                    {'count': 1}
                ]

                release = html_release()
                await publish_release(ledger, release)
                activated = await transport.call(
                    {
                        'operation': 'management',
                        'request': {
                            'operation': 'activate',
                            'command_id': str(uuid4()),
                            'family': 'writing',
                            'revision': 0,
                            'evaluation_id': release.evaluation.id,
                            'publication_id': release.publication.id,
                            'point_id': release.points.id,
                        },
                    }
                )
                assert 'result' in activated
                started = await transport.http(
                    '/api/sessions',
                    body={'command_id': str(uuid4()), 'family': 'writing'},
                    cookie=renewed_cookie,
                )
                assert started['status'] == 201
                session = body(started)
                pair = session['pair']
                assert isinstance(pair, dict)
                assert isinstance(pair['a'], dict) and isinstance(pair['b'], dict)
                documents: list[str] = []
                for side in ('a', 'b'):
                    pane = pair[side]
                    assert isinstance(pane, dict) and pane['render'] == 'html'
                    url = pane['url']
                    assert isinstance(url, str)

                    rendered = await transport.http(urlsplit(url).path, renderer=True)
                    assert rendered['status'] == 200
                    content = rendered['body']
                    assert isinstance(content, str)
                    documents.append(content)

                assert set(documents) == {item.content for item in release.rendered}
                vote: dict[str, JsonValue] = {
                    'command_id': str(uuid4()),
                    'lease_id': pair['id'],
                    'capability': pair['capability'],
                    'choice': 'a',
                }

                accepted = await transport.http(
                    '/api/votes', body=vote, cookie=renewed_cookie
                )
                assert accepted['status'] == 200
                repeated = await transport.http(
                    '/api/votes', body=vote, cookie=renewed_cookie
                )
                assert repeated['status'] == 200
                assert body(repeated) == body(accepted)
                receipt = {
                    key: value for key, value in vote.items() if key != 'capability'
                }
                resolved = await transport.http(
                    '/api/votes/resolve', body=receipt, cookie=renewed_cookie
                )
                assert resolved['status'] == 200
                assert body(resolved) == body(accepted)
                assert await transport.rows(
                    'SELECT count(*) AS count FROM vote_receipts'
                ) == [{'count': 1}]

                acknowledgment = body(accepted)
                withdrawn = await transport.http(
                    '/api/sessions/withdraw',
                    body={
                        'command_id': str(uuid4()),
                        'session_id': acknowledgment['session_id'],
                        'acknowledgment': acknowledgment['acknowledgment'],
                    },
                    cookie=renewed_cookie,
                )
                assert withdrawn['status'] == 204
                resumed = await transport.http(
                    '/api/sessions/resume',
                    body={'command_id': str(uuid4()), 'session_id': session['id']},
                    cookie=renewed_cookie,
                )
                assert resumed['status'] == 200
                assert body(resumed)['votes'] == 0
                assert await transport.rows(
                    'SELECT count(*) AS count FROM vote_receipts'
                ) == [{'count': 1}]

                for index in range(251):
                    await transport.execute(
                        Statement(
                            sql="""
                                INSERT INTO quota_windows(operation, scope, subject,
                                    window_ms, window_start, consumed, expires_at)
                                VALUES ('admit', 'exact', ?, 86400000, 0, 1, 1)
                            """,
                            params=(f'expired-{index}',),
                        )
                    )

                scheduled = await transport.call({'operation': 'scheduled'})
                assert scheduled == {'completed': True}
                assert await transport.rows(
                    'SELECT subject FROM quota_windows WHERE expires_at <= ?',
                    time.time_ns() // 1_000_000,
                ) == [{'subject': 'expired-250'}]
                assert await transport.rows(
                    'SELECT count(*) AS count FROM vote_receipts'
                ) == [{'count': 1}]

        with NativeRuntime(tmp_path).owner() as runtime:
            asyncio.run(scenario(runtime))

        assert (tmp_path / 'pylock.toml').read_bytes() == (
            ROOT / 'arena' / 'pylock.toml'
        ).read_bytes()
