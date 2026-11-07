"""SQL statements through SQLite or Cloudflare's D1 REST API."""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from assistant_bench.messages import sentence


API_BASE_URL = 'https://api.cloudflare.com/client/v4'
TIMEOUT_SEC = 120.0
"""Long enough to upload a statement of several megabytes over a slow link."""

type Param = str | int | float | None
type Row = dict[str, JsonValue]


class D1Error(Exception):
    """An unsuccessful D1 response or a transport failure, with the reason."""


@dataclass(frozen=True)
class Result:
    rows: list[Row]
    changes: int
    """Rows the statement and its triggers inserted, updated, or deleted."""


class Database(Protocol):
    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        """Run one statement in auto-commit, binding `params` to its `?` slots."""
        ...


class SQLiteDatabase:
    """The database protocol on an autocommit SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        before = self.connection.total_changes
        cursor = self.connection.execute(sql, list(params))
        columns = [column[0] for column in cursor.description or ()]
        rows: list[Row] = [dict(zip(columns, row, strict=True)) for row in cursor]
        return Result(rows=rows, changes=self.connection.total_changes - before)


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
            raise D1Error(f'D1 could not be reached: {sentence(str(error))}') from error

        try:
            payload = _Response.model_validate_json(response.content)
        except ValidationError as error:
            raise D1Error(
                f'D1 answered HTTP {response.status_code} without a result.'
            ) from error

        if not payload.success or response.is_error or len(payload.result) != 1:
            messages = ' '.join(sentence(error.message) for error in payload.errors)
            raise D1Error(
                f'D1 refused the statement with HTTP {response.status_code}. '
                f'{messages}'.rstrip()
            )

        statement = payload.result[0]
        return Result(rows=statement.results, changes=statement.meta.changes)
