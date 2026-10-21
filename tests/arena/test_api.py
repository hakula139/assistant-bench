from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from assistant_bench.arena import api
from assistant_bench.arena.service import Arena
from assistant_bench.arena.store import VoteStore
from support import Clock, make_arena


ORIGIN = {'Origin': api.APP_ORIGIN}


@pytest.fixture
def arena(families_dir: Path, clock: Clock, store: VoteStore) -> Arena:
    return make_arena(
        families_dir, clock, store, texts={'large': '<p>L</p>', 'small': '<p>S</p>'}
    )


@pytest.fixture
def client(arena: Arena) -> TestClient:
    return TestClient(api.create_app(arena), base_url=api.APP_ORIGIN)


class TestCreateApp:
    def test_runs_a_session_from_start_to_undo(self, client: TestClient) -> None:
        assert client.get('/api/families').json() == ['f']

        started = client.post('/api/sessions', json={'family': 'f'}, headers=ORIGIN)
        assert started.status_code == 201
        session = started.json()
        token = session['pair']['token']

        voted = client.post(
            f'/api/pairs/{token}/vote',
            json={'choice': 'both_bad', 'flags': ['script'], 'note': 'Mixed.'},
            headers=ORIGIN,
        )
        assert voted.status_code == 200
        assert voted.json()['votes'] == 1

        undone = client.post(f'/api/sessions/{session["id"]}/undo', headers=ORIGIN)
        assert undone.json()['votes'] == 0
        assert undone.json()['pair']['a'] == session['pair']['a']

        resumed = client.post(f'/api/sessions/{session["id"]}/pair', headers=ORIGIN)
        assert resumed.json() == undone.json()

    @pytest.mark.parametrize(
        'headers', [{}, {'Origin': 'http://127.0.0.1:1'}, {'Origin': 'null'}]
    )
    def test_rejects_a_write_from_another_origin(
        self, client: TestClient, headers: dict[str, str]
    ) -> None:
        response = client.post('/api/sessions', json={'family': 'f'}, headers=headers)

        assert response.status_code == 403

    def test_rejects_another_host(self, arena: Arena) -> None:
        client = TestClient(api.create_app(arena), base_url='http://localhost:8790')

        assert client.get('/api/families').status_code == 400

    @pytest.mark.parametrize(
        ('path', 'body', 'status'),
        [
            ('/api/sessions', {'family': 'nope'}, 404),
            ('/api/pairs/nope/vote', {'choice': 'a'}, 404),
            ('/api/sessions/nope/undo', None, 404),
            ('/api/pairs/nope/vote', {'choice': 'left'}, 422),
        ],
    )
    def test_answers_bad_writes_without_detail(
        self, client: TestClient, path: str, body: Any, status: int
    ) -> None:
        response = client.post(path, json=body, headers=ORIGIN)

        assert response.status_code == status
        if status == 404:
            assert response.json() == {'detail': 'not found'}

    def test_refuses_to_undo_an_empty_session(self, client: TestClient) -> None:
        session = client.post('/api/sessions', json={'family': 'f'}, headers=ORIGIN)

        response = client.post(
            f'/api/sessions/{session.json()["id"]}/undo', headers=ORIGIN
        )

        assert response.status_code == 409

    def test_serves_the_built_vote_page(self, arena: Arena, tmp_path: Path) -> None:
        site = tmp_path / 'dist'
        site.mkdir()
        (site / 'index.html').write_text('<h1>Vote</h1>')
        client = TestClient(api.create_app(arena, site), base_url=api.APP_ORIGIN)

        assert client.get('/').text == '<h1>Vote</h1>'

    def test_sends_the_vote_policy_on_the_vote_page_alone(
        self, arena: Arena, tmp_path: Path
    ) -> None:
        site = tmp_path / 'dist'
        (site / 'vote').mkdir(parents=True)
        (site / 'index.html').write_text('<h1>Boards</h1>')
        (site / 'vote' / 'index.html').write_text('<h1>Vote</h1>')
        client = TestClient(api.create_app(arena, site), base_url=api.APP_ORIGIN)

        page = client.get('/vote/')
        board = client.get('/')

        assert page.text == '<h1>Vote</h1>'
        assert page.headers['content-security-policy'] == (
            "default-src 'none'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self'; "
            "connect-src 'self'; frame-src http://127.0.0.1:8791; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        )
        assert 'content-security-policy' not in board.headers


class TestCreateRenderApp:
    def test_serves_an_output_under_the_sandbox_policy(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(
            families_dir,
            clock,
            store,
            texts={'large': '<p>L</p>', 'small': '<p>S</p>'},
            render='html',
        )
        view = arena.start('f')
        assert view.pair is not None
        url = view.pair.a.url  # type: ignore[union-attr]
        client = TestClient(api.create_render_app(arena), base_url=api.RENDER_ORIGIN)

        response = client.get(f'/outputs/{url.rsplit("/", 1)[1]}')

        assert response.text in {'<p>L</p>', '<p>S</p>'}
        assert response.headers['content-security-policy'] == (
            "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; "
            'frame-ancestors http://127.0.0.1:8790'
        )
        assert client.get('/outputs/nope').status_code == 404


class TestServe:
    def test_binds_both_origins_to_loopback(
        self, arena: Arena, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bound: list[tuple[str, int]] = []

        async def serve(server: uvicorn.Server) -> None:
            bound.append((server.config.host, server.config.port))

        monkeypatch.setattr(uvicorn.Server, 'serve', serve)

        api.serve(arena, tmp_path)

        assert bound == [('127.0.0.1', 8790), ('127.0.0.1', 8791)]
