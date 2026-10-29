"""The arena's D1 databases, reached over Cloudflare's REST API."""

import os
import tomllib
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Protocol

import httpx
import typer
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from assistant_bench.messages import sentence
from assistant_bench.paths import REPO_ROOT


WRANGLER_CONFIG = REPO_ROOT / 'web' / 'wrangler.toml'
TOKEN_FILE_VAR = 'BENCH_D1_TOKEN_FILE'
API_BASE_URL = 'https://api.cloudflare.com/client/v4'
TIMEOUT_SEC = 120.0
"""Long enough to upload a statement of several megabytes over a slow link."""

TargetName = Literal['staging', 'production']
Target = Annotated[
    TargetName, typer.Argument(help='The vote store: staging or production.')
]

type Param = str | int | float | None
type Row = dict[str, JsonValue]


class D1Error(Exception):
    """A request D1 refused or that never reached it, with the reason."""


@dataclass(frozen=True)
class Result:
    rows: list[Row]
    changes: int
    """Rows the statement inserted, updated, or deleted."""


class Database(Protocol):
    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        """Run one statement in auto-commit, binding `params` to its `?` slots."""
        ...


# ── REST Client ──


class _Message(BaseModel):
    message: str


class _Meta(BaseModel):
    changes: int = 0


class _Statement(BaseModel):
    results: list[Row] = []
    meta: _Meta = _Meta()


class _Response(BaseModel):
    success: bool
    errors: list[_Message] = []
    result: list[_Statement] = []


class RestDatabase:
    """One database through the `/query` endpoint, one statement per request."""

    def __init__(self, client: httpx.Client, account_id: str, database_id: str):
        self._client = client
        self._path = f'/accounts/{account_id}/d1/database/{database_id}/query'

    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        try:
            response = self._client.post(
                self._path, json={'sql': sql, 'params': list(params)}
            )
        except httpx.TransportError as error:
            raise D1Error(
                f'D1 could not be reached: {sentence(str(error))} '
                'Every command is safe to rerun.'
            ) from error

        try:
            payload = _Response.model_validate_json(response.content)
        except ValidationError as error:
            raise D1Error(
                f'D1 answered HTTP {response.status_code} without a result.'
            ) from error

        if not payload.success or response.is_error or len(payload.result) != 1:
            messages = ' '.join(
                error.message.rstrip('.') + '.' for error in payload.errors
            )
            raise D1Error(
                f'D1 refused the statement with HTTP {response.status_code}. '
                f'{messages}'.rstrip()
            )

        statement = payload.result[0]
        return Result(rows=statement.results, changes=statement.meta.changes)


# ── Targets ──


class _Binding(BaseModel):
    model_config = ConfigDict(extra='allow')

    binding: str
    database_name: str
    database_id: str | None = None


class _Environment(BaseModel):
    model_config = ConfigDict(extra='allow')

    d1_databases: list[_Binding] = []


class _Config(BaseModel):
    model_config = ConfigDict(extra='allow')

    account_id: str
    env: dict[str, _Environment] = {}


def account_id() -> str:
    """Return the Cloudflare account that `web/wrangler.toml` names."""
    return _config().account_id


@contextmanager
def connect(target: TargetName) -> Generator[Database]:
    """Open the target's database with the token in the file `.env` names.

    Raises
    ------
    ValueError
        `web/wrangler.toml` records no database ID for the target, or `.env`
        names no token file.
    OSError
        The token file cannot be read.
    """
    config = _config()
    databases = [
        binding
        for binding in config.env.get(target, _Environment()).d1_databases
        if binding.binding == 'DB'
    ]
    if len(databases) != 1:
        raise ValueError(
            f'Target `{target}` needs one `DB` binding in `web/wrangler.toml`.'
        )

    database = databases[0]
    if database.database_id is None:
        raise ValueError(
            f'Target `{target}` has no D1 database ID in `web/wrangler.toml`. '
            f'Create it with `wrangler d1 create {database.database_name}` and '
            'record its ID there.'
        )

    path = os.environ.get(TOKEN_FILE_VAR)
    if not path:
        raise ValueError(
            f'Set `{TOKEN_FILE_VAR}` in `.env` to the path of a file holding a '
            'Cloudflare API token with D1 Edit.'
        )

    token = Path(path).read_text().strip()
    with httpx.Client(
        base_url=API_BASE_URL,
        headers={'Authorization': f'Bearer {token}'},
        timeout=TIMEOUT_SEC,
    ) as client:
        yield RestDatabase(client, config.account_id, database.database_id)


def _config() -> _Config:
    return _Config.model_validate(tomllib.loads(WRANGLER_CONFIG.read_text()))
