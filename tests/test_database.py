import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from assistant_bench.database import API_BASE_URL, D1Error, RestDatabase, Result


ACCOUNT = 'synthetic-account'
DATABASE = 'synthetic-database'


def rest_database(
    handler: Callable[[httpx.Request], httpx.Response],
) -> RestDatabase:
    client = httpx.Client(base_url=API_BASE_URL, transport=httpx.MockTransport(handler))
    return RestDatabase(client, ACCOUNT, DATABASE)


def answer(status: int, payload: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=payload)


class TestRestDatabase:
    def test_posts_one_statement_and_reads_its_rows(self) -> None:
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

        assert result == Result(rows=[{'id': 7, 'family': 'writing-zh'}])
        (request,) = requests
        assert request.method == 'POST'
        assert str(request.url) == (
            f'{API_BASE_URL}/accounts/{ACCOUNT}/d1/database/{DATABASE}/query'
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

    def test_raises_a_transport_failure_with_its_reason(self) -> None:
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout('timed out', request=request)

        with pytest.raises(D1Error) as raised:
            rest_database(unreachable).execute('SELECT 1')

        assert str(raised.value) == 'D1 could not be reached: timed out.'


class TestCoreHelpers:
    def test_compile_statement_expands_in_clauses_and_normalizes_booleans(self) -> None:
        from sqlalchemy import literal, select

        from assistant_bench import schema
        from assistant_bench.database import compile_statement

        stmt = select(schema.Runs.id).where(
            schema.Runs.status.in_(['prepared', 'running']),
            literal(True) == literal(1),
        )
        sql, params = compile_statement(stmt)

        assert '?' in sql
        assert params == ['prepared', 'running', 1, 1]
        assert all(not isinstance(value, bool) for value in params)

    def test_compile_statement_rejects_unsupported_parameter_types(self) -> None:
        from sqlalchemy import literal, select

        from assistant_bench.database import compile_statement

        with pytest.raises(TypeError, match='Unsupported SQL parameter type'):
            compile_statement(select(literal({'nested': 1})))

    def test_fetch_helpers_and_insert_missing_chunking(self) -> None:
        from pydantic import BaseModel, ConfigDict
        from sqlalchemy import select

        from assistant_bench import schema
        from assistant_bench.database import (
            Param,
            fetch_all,
            fetch_one,
            fetch_scalar,
            insert_missing,
        )
        from support import private_database

        class _Head(BaseModel):
            model_config = ConfigDict(extra='forbid', frozen=True)

            scope: str
            revision: int

        with private_database() as database:
            insert_missing(database, schema.SourceHeads, [])
            rows: list[dict[str, Param]] = [
                {'scope': f'scope-{i:03d}', 'revision': i} for i in range(60)
            ]
            insert_missing(database, schema.SourceHeads, rows)
            insert_missing(database, schema.SourceHeads, rows[:5])

            all_heads = fetch_all(
                database,
                select(schema.SourceHeads.scope, schema.SourceHeads.revision).order_by(
                    schema.SourceHeads.scope
                ),
                _Head,
            )
            assert len(all_heads) == 60
            assert all_heads[0] == _Head(scope='scope-000', revision=0)

            one = fetch_one(
                database,
                select(schema.SourceHeads.scope, schema.SourceHeads.revision).where(
                    schema.SourceHeads.scope == 'scope-042'
                ),
                _Head,
            )
            assert one == _Head(scope='scope-042', revision=42)
            assert (
                fetch_one(
                    database,
                    select(schema.SourceHeads.scope, schema.SourceHeads.revision).where(
                        schema.SourceHeads.scope == 'missing'
                    ),
                    _Head,
                )
                is None
            )

            assert (
                fetch_scalar(
                    database,
                    select(schema.SourceHeads.revision).where(
                        schema.SourceHeads.scope == 'scope-042'
                    ),
                    int,
                )
                == 42
            )
            assert (
                fetch_scalar(
                    database,
                    select(schema.SourceHeads.revision).where(
                        schema.SourceHeads.scope == 'missing'
                    ),
                    int,
                )
                is None
            )
