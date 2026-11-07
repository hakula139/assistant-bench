"""The local vote store, its exclusive access and the Worker's lifecycle."""

import fcntl
import os
import secrets
import signal
import sqlite3
import subprocess
import time
from collections.abc import Generator, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from types import FrameType

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from assistant_bench.arena.d1 import SQLiteDatabase
from assistant_bench.messages import InputError
from assistant_bench.paths import REPO_ROOT, data_dir


DATABASE_GLOB = 'v3/d1/miniflare-D1DatabaseObject/*.sqlite'


class _Keys(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    ACTOR_KEY: str = Field(min_length=1)
    IP_TAG_KEY: str = Field(min_length=1)


def directory() -> Path:
    """Return Wrangler's persistence root in the data directory."""
    return data_dir() / 'arena'


@contextmanager
def lock() -> Generator[None]:
    """Hold the store lock, refusing another server or local command."""
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'serve.lock').open('a') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InputError(
                'The local vote store is in use. Stop `bench serve` before '
                'running another local command or closing a round.'
            ) from error
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def database_path() -> Path:
    """Locate the single D1 database Wrangler initialized."""
    paths = [
        path
        for path in directory().glob(DATABASE_GLOB)
        if path.name != 'metadata.sqlite'
    ]
    if not paths:
        raise InputError('No local votes yet. Run `uv run bench serve` first.')
    if len(paths) != 1:
        raise InputError(
            f'Local vote storage in `{directory()}` holds several D1 databases. '
            'Keep only the local voting database before continuing.'
        )
    return paths[0]


@contextmanager
def database() -> Generator[SQLiteDatabase]:
    """Open the initialized store while its caller holds the exclusive lock."""
    with closing(sqlite3.connect(database_path(), autocommit=True)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        yield SQLiteDatabase(connection)


@contextmanager
def connect() -> Generator[SQLiteDatabase]:
    """Hold exclusive access to the initialized local vote store."""
    with lock(), database() as store:
        yield store


def keys() -> dict[str, str]:
    """Create the local signing keys once, readable only by their owner."""
    path = directory() / 'keys.json'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            return _Keys.model_validate_json(path.read_bytes()).model_dump()
        except OSError, ValidationError:
            raise InputError(
                f'The local signing keys in `{path}` cannot be read. '
                'Restore the valid key file before continuing.'
            ) from None

    created = _Keys(ACTOR_KEY=secrets.token_hex(32), IP_TAG_KEY=secrets.token_hex(32))
    with os.fdopen(descriptor, 'w') as file:
        file.write(created.model_dump_json() + '\n')
    return created.model_dump()


def build() -> None:
    """Build the site served alongside the local vote API."""
    _run(['pnpm', '--filter', 'web', 'build'])


def migrate() -> None:
    """Apply the same migrations as the hosted Worker, creating its local store."""
    _run(
        [
            'pnpm',
            '--filter',
            'web',
            'exec',
            'wrangler',
            'd1',
            'migrations',
            'apply',
            'DB',
            '--local',
            '--env',
            'local',
            '--persist-to',
            str(directory().resolve()),
        ],
    )


@contextmanager
def interrupts() -> Generator[None]:
    """Translate service stop signals into cleanup and restore prior handlers."""

    def interrupt(number: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    previous = {
        number: signal.signal(number, interrupt)
        for number in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def serve(signing_keys: Mapping[str, str]) -> None:
    """Run the local Worker and stop its process group before returning."""
    command = [
        'pnpm',
        '--filter',
        'web',
        'exec',
        'wrangler',
        'dev',
        '--env',
        'local',
        '--persist-to',
        str(directory().resolve()),
    ]
    try:
        with _process(command, env=os.environ | dict(signing_keys)) as process:
            result = process.wait()
            if result:
                raise InputError(f'The local vote server exited with code {result}.')
    except KeyboardInterrupt:
        pass


@contextmanager
def _process(
    command: Sequence[str], *, env: Mapping[str, str] | None = None
) -> Generator[subprocess.Popen[bytes]]:
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        deadline = time.monotonic() + 10
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            time.sleep(0.01)


def _run(command: Sequence[str]) -> None:
    with _process(command) as process:
        result = process.wait()
        if result:
            raise InputError(f'Local voting preparation exited with code {result}.')
