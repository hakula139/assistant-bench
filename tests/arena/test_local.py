import json
import os
import secrets
import signal
import sqlite3
import subprocess
from collections.abc import Generator
from contextlib import AbstractContextManager, closing, contextmanager
from pathlib import Path
from typing import Any, Literal

import pytest

from assistant_bench.arena import d1, local, stores
from assistant_bench.messages import InputError


@pytest.fixture
def database_file() -> Path:
    path = local.directory() / 'v3/d1/miniflare-D1DatabaseObject/example.sqlite'
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.execute('CREATE TABLE example (value TEXT)')
    path.with_name('metadata.sqlite').touch()
    return path


class TestLock:
    def test_refuses_a_second_server_and_local_command_then_releases(
        self, database_file: Path
    ) -> None:
        with local.lock():
            for operation in (local.lock, local.connect):
                with pytest.raises(InputError, match='Stop `bench serve`'), operation():
                    pytest.fail('The locked store admitted another operation.')

        with local.connect() as database:
            database.execute('INSERT INTO example VALUES (?)', ['recorded'])
        with closing(sqlite3.connect(database_file)) as connection:
            assert connection.execute('SELECT value FROM example').fetchall() == [
                ('recorded',)
            ]


class TestDatabasePath:
    def test_requires_an_initialized_store_and_refuses_ambiguous_databases(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(InputError, match='Run `uv run bench serve` first'):
            local.database_path()
        first = local.directory() / 'v3/d1/miniflare-D1DatabaseObject/first.sqlite'
        first.parent.mkdir(parents=True)
        first.touch()
        first.with_name('second.sqlite').touch()

        with pytest.raises(InputError, match='holds several D1 databases'):
            local.database_path()


class TestKeys:
    def test_creates_private_keys_once_and_reuses_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        generated = iter(['synthetic-actor-key', 'synthetic-ip-key'])

        def generate(size: int) -> str:
            return next(generated)

        monkeypatch.setattr(secrets, 'token_hex', generate)

        with local.lock():
            first = local.keys()
            second = local.keys()

        path = local.directory() / 'keys.json'
        assert (
            first
            == second
            == {
                'ACTOR_KEY': 'synthetic-actor-key',
                'IP_TAG_KEY': 'synthetic-ip-key',
            }
        )
        assert json.loads(path.read_text()) == first
        assert path.stat().st_mode & 0o777 == 0o600

    def test_refuses_invalid_keys_without_revealing_their_content(self) -> None:
        with local.lock():
            path = local.directory() / 'keys.json'
            path.write_text('{"ACTOR_KEY":"synthetic-private-material"}')

            with pytest.raises(InputError, match='local signing keys') as raised:
                local.keys()

        assert 'synthetic-private-material' not in str(raised.value)
        assert raised.value.__cause__ is None


class TestBuild:
    @pytest.mark.parametrize('operation', [local.build, local.migrate])
    def test_reports_a_failed_preparation_command(
        self, operation: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def run(*args: Any, **kwargs: Any) -> None:
            assert kwargs['stdin'] == subprocess.DEVNULL
            raise subprocess.CalledProcessError(7, args[0])

        monkeypatch.setattr(subprocess, 'run', run)

        with pytest.raises(InputError, match='exited with code 7'):
            operation()


class TestServe:
    @pytest.mark.parametrize('exit', ['normal', 'interrupt', 'failure', 'stalled'])
    def test_passes_keys_only_in_environment_and_waits_for_group_cleanup(
        self,
        exit: Literal['normal', 'interrupt', 'failure', 'stalled'],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launched: list[tuple[list[str], dict[str, Any]]] = []
        signals: list[tuple[int, int]] = []
        waits: list[float | None] = []
        signing = {'ACTOR_KEY': 'synthetic-actor-key', 'IP_TAG_KEY': 'synthetic-ip-key'}

        class Process:
            pid = 123

            def __init__(self, command: list[str], **kwargs: Any):
                launched.append((command, kwargs))

            def wait(self, timeout: float | None = None) -> int:
                waits.append(timeout)
                if len(waits) == 1:
                    if exit == 'interrupt':
                        raise KeyboardInterrupt
                    return 1 if exit == 'failure' else 0
                if exit == 'stalled' and len(waits) == 2:
                    assert timeout is not None
                    raise subprocess.TimeoutExpired('wrangler', timeout)
                return 0

        monkeypatch.setattr(subprocess, 'Popen', Process)

        def kill(pid: int, number: int) -> None:
            signals.append((pid, number))

        monkeypatch.setattr(os, 'killpg', kill)

        if exit == 'failure':
            with pytest.raises(InputError, match='server exited with code 1'):
                local.serve(signing)
        else:
            local.serve(signing)

        command, options = launched[0]
        assert all(value not in ' '.join(command) for value in signing.values())
        assert options['env']['ACTOR_KEY'] == signing['ACTOR_KEY']
        assert options['env']['IP_TAG_KEY'] == signing['IP_TAG_KEY']
        assert options['start_new_session']
        expected = [(123, signal.SIGTERM)]
        if exit == 'stalled':
            expected.append((123, signal.SIGKILL))
        assert signals == expected
        assert waits == ([None, 10, None] if exit == 'stalled' else [None, 10])


class TestConnect:
    def test_dispatches_local_without_creating_a_remote_client(
        self, database_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def remote(target: str) -> AbstractContextManager[d1.Database]:
            pytest.fail(f'Local voting constructed a remote client for {target}.')

        monkeypatch.setattr(d1, 'connect', remote)

        with stores.connect('local') as database:
            database.execute('INSERT INTO example VALUES (?)', ['local-only'])

        with closing(sqlite3.connect(database_file)) as connection:
            assert connection.execute('SELECT value FROM example').fetchall() == [
                ('local-only',)
            ]

    @pytest.mark.parametrize('target', ['production', 'staging'])
    def test_dispatches_the_remote_target(
        self, target: Literal['production', 'staging'], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        @contextmanager
        def remote(selected: str) -> Generator[None]:
            seen.append(selected)
            yield

        monkeypatch.setattr(d1, 'connect', remote)

        with stores.connect(target):
            pass

        assert seen == [target]
