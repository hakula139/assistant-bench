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
