"""SQL statements through SQLite or Cloudflare's D1 REST API."""

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from itertools import batched, chain
from typing import Any, Protocol, cast

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.compiler import SQLCompiler

from assistant_bench.messages import sentence


API_BASE_URL = 'https://api.cloudflare.com/client/v4'
TIMEOUT_SEC = 120.0
"""Long enough to upload a statement of several megabytes over a slow link."""

_MAX_PARAMS = 100
"""D1's limit on the parameters one statement binds."""

_DIALECT = sqlite.dialect(paramstyle='qmark')

type Param = str | int | float | None
type Row = dict[str, JsonValue]


class D1Error(Exception):
    """An unsuccessful D1 response or a transport failure, with the reason."""


@dataclass(frozen=True)
class Result:
    rows: list[Row]


class Database(Protocol):
    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        """Run one statement in auto-commit, binding `params` to its `?` slots."""
        ...


# ── Core Helpers ──


@cache
def _scalar_adapter(scalar_type: Any) -> TypeAdapter[Any]:
    return TypeAdapter(scalar_type)


def compile_statement(statement: ClauseElement) -> tuple[str, list[Param]]:
    """Compile a SQLAlchemy Core statement to qmark SQL and D1-safe parameters."""
    compiled = statement.compile(
        dialect=_DIALECT,
        compile_kwargs={'render_postcompile': True},
    )
    if not isinstance(compiled, SQLCompiler):
        raise TypeError(f'Expected a SQLCompiler, got {type(compiled).__name__}.')
    raw_params = compiled.params or {}
    params: list[Param] = []
    for key in compiled.positiontup or ():
        value: object = raw_params[key]
        bind = compiled.binds.get(key)
        if bind is not None:
            processor = bind.type.bind_processor(_DIALECT)
            if processor is not None:
                value = processor(value)
        if isinstance(value, bool):
            value = int(value)
        if value is not None and not isinstance(value, (str, int, float)):
            raise TypeError(
                f'Unsupported SQL parameter type {type(value).__name__} for {key!r}.'
            )
        params.append(value)
    return str(compiled), params


def execute(database: Database, statement: ClauseElement) -> Result:
    sql, params = compile_statement(statement)
    return database.execute(sql, params)


def fetch_all[T: BaseModel](
    database: Database, statement: ClauseElement, model: type[T]
) -> list[T]:
    return [model.model_validate(row) for row in execute(database, statement).rows]


def fetch_one[T: BaseModel](
    database: Database, statement: ClauseElement, model: type[T]
) -> T | None:
    rows = execute(database, statement).rows
    return model.model_validate(rows[0]) if rows else None


def fetch_scalar[T](
    database: Database, statement: ClauseElement, scalar_type: type[T]
) -> T | None:
    rows = execute(database, statement).rows
    if not rows:
        return None
    value = next(iter(rows[0].values()))
    if value is None:
        return None
    return cast(T, _scalar_adapter(cast(Any, scalar_type)).validate_python(value))


def insert_missing(
    database: Database,
    table: type[DeclarativeBase],
    rows: Iterable[Mapping[str, Param]],
) -> None:
    """Insert rows missing from the table in statements within D1's parameter limit."""
    iterator = iter(rows)
    first = next(iterator, None)
    if first is None:
        return
    per_statement = _MAX_PARAMS // len(first)
    for chunk in batched(chain((first,), iterator), per_statement, strict=False):
        execute(
            database,
            sqlite.insert(table).values(list(chunk)).on_conflict_do_nothing(),
        )


# ── Adapters ──


class SQLiteDatabase:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        cursor = self.connection.execute(sql, list(params))
        columns = [column[0] for column in cursor.description or ()]
        rows: list[Row] = [dict(zip(columns, row, strict=True)) for row in cursor]
        return Result(rows=rows)


class _Message(BaseModel):
    message: str


class _Statement(BaseModel):
    results: list[Row] = []


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
        return Result(rows=statement.results)
