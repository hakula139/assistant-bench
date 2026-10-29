import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import httpx
import pytest

from assistant_bench.arena import d1
from assistant_bench.arena.d1 import D1Error, RestDatabase, Result


ACCOUNT = 'synthetic-account'
DATABASE = 'synthetic-database'


def rest_database(
    handler: Callable[[httpx.Request], httpx.Response],
) -> RestDatabase:
    client = httpx.Client(
        base_url=d1.API_BASE_URL, transport=httpx.MockTransport(handler)
    )
    return RestDatabase(client, ACCOUNT, DATABASE)


def answer(status: int, payload: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=payload)


class TestRestDatabase:
    def test_posts_one_statement_and_reads_its_rows_and_changes(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    'success': True,
                    'errors': [],
                    'messages': [],
                    'result': [
                        {
                            'success': True,
                            'results': [{'id': 7, 'family': 'writing-zh'}],
                            'meta': {'changes': 1, 'duration': 0.2},
                        }
                    ],
                },
            )

        result = rest_database(handle).execute(
            'SELECT id, family FROM votes WHERE id = ? AND family = ?',
            [7, 'writing-zh'],
        )

        assert result == Result(rows=[{'id': 7, 'family': 'writing-zh'}], changes=1)
        (request,) = requests
        assert request.method == 'POST'
        assert str(request.url) == (
            f'{d1.API_BASE_URL}/accounts/{ACCOUNT}/d1/database/{DATABASE}/query'
        )
        assert json.loads(request.content) == {
            'sql': 'SELECT id, family FROM votes WHERE id = ? AND family = ?',
            'params': [7, 'writing-zh'],
        }

    @pytest.mark.parametrize(
        ('status', 'payload', 'message'),
        [
            (
                400,
                {
                    'success': False,
                    'errors': [
                        {'code': 7500, 'message': 'no such table: votes: SQLITE_ERROR'}
                    ],
                    'result': [],
                },
                'D1 refused the statement with HTTP 400. '
                'no such table: votes: SQLITE_ERROR.',
            ),
            (
                403,
                {
                    'success': False,
                    'errors': [{'code': 10000, 'message': 'Authentication error'}],
                },
                'D1 refused the statement with HTTP 403. Authentication error.',
            ),
            (
                502,
                'Bad gateway',
                'D1 answered HTTP 502 without a result.',
            ),
        ],
        ids=['statement', 'token', 'gateway'],
    )
    def test_raises_the_api_messages_of_a_refused_request(
        self, status: int, payload: Any, message: str
    ) -> None:
        database = rest_database(answer(status, payload))

        with pytest.raises(D1Error) as raised:
            database.execute('SELECT 1')

        assert str(raised.value) == message


class TestConnect:
    @pytest.fixture
    def config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        path = tmp_path / 'wrangler.toml'
        path.write_text(
            dedent(f"""
                name = "preview"
                account_id = "{ACCOUNT}"

                [[env.staging.d1_databases]]
                binding = "DB"
                database_name = "arena-staging"
                database_id = "{DATABASE}"

                [[env.production.d1_databases]]
                binding = "DB"
                database_name = "arena-production"
            """)
        )
        monkeypatch.setattr(d1, 'WRANGLER_CONFIG', path)
        return path

    def test_queries_the_target_database_with_the_token_from_its_file(
        self, config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = tmp_path / 'd1-token'
        token.write_text('synthetic-token\n')
        monkeypatch.setenv(d1.TOKEN_FILE_VAR, str(token))
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={'success': True, 'result': [{}]})

        client = httpx.Client

        def mocked(**kwargs: Any) -> httpx.Client:
            return client(transport=httpx.MockTransport(handle), **kwargs)

        monkeypatch.setattr(httpx, 'Client', mocked)

        with d1.connect('staging') as database:
            database.execute('SELECT 1')

        (request,) = requests
        assert request.headers['Authorization'] == 'Bearer synthetic-token'
        assert request.url.path == (
            f'/client/v4/accounts/{ACCOUNT}/d1/database/{DATABASE}/query'
        )

    def test_names_the_create_command_for_a_target_without_an_id(
        self, config: Path
    ) -> None:
        with pytest.raises(ValueError) as raised, d1.connect('production'):
            pass

        assert str(raised.value) == (
            'Target `production` has no D1 database ID in `web/wrangler.toml`. '
            'Create it with `wrangler d1 create arena-production` and record its '
            'ID there.'
        )

    def test_names_the_token_variable_when_env_sets_none(self, config: Path) -> None:
        with pytest.raises(ValueError, match=d1.TOKEN_FILE_VAR), d1.connect('staging'):
            pass
