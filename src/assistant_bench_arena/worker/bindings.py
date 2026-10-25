"""Narrow native Worker binding calls with validated result boundaries."""

import re
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, TypeAdapter, ValidationError
from pyodide.ffi import JsException, JsNull

from assistant_bench_arena.admission import ChallengeRejected
from assistant_bench_arena.ledger_models import Row, SqlValue, Statement
from assistant_bench_arena.transport import CommandRejected, Rejection


if TYPE_CHECKING:
    from js import ReadableStream


_REJECTION = TypeAdapter(Rejection)
_GUARD = re.compile(
    r'^Error: D1_ERROR: ([a-z_]+): SQLITE_CONSTRAINT '
    r'\(extended: SQLITE_CONSTRAINT_TRIGGER\)$'
)


class NativeStatement(Protocol):
    def bind(self, *params: SqlValue) -> NativeStatement: ...
    async def all(self) -> object: ...


class NativeDatabase(Protocol):
    def prepare(self, sql: str) -> NativeStatement: ...


class DatabaseResult(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)

    results: list[Row]


class D1Transport:
    def __init__(self, database: NativeDatabase) -> None:
        self.database: NativeDatabase = database

    async def execute(self, statement: Statement) -> None:
        await self._run(statement)

    async def query(self, statement: Statement) -> list[Row]:
        return (await self._run(statement)).results

    async def _run(self, statement: Statement) -> DatabaseResult:
        try:
            value = (
                await self.database.prepare(statement.sql).bind(*statement.params).all()
            )
        except JsException as error:
            reason = _rejection(error)

            if reason is not None:
                raise CommandRejected(reason) from None

            raise

        return DatabaseResult.model_validate(value)


def _rejection(error: JsException) -> Rejection | None:
    match = _GUARD.fullmatch(str(error))

    if match is not None:
        try:
            return _REJECTION.validate_python(match[1])
        except ValidationError:
            pass

    return None


class NativeLimiter(Protocol):
    async def limit(self, options: dict[str, str]) -> object: ...


class LimitResult(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)

    success: StrictBool


class RateLimited(Exception):
    """An approximate native request budget has been exhausted."""


async def enforce_limit(limiter: NativeLimiter, key: str) -> None:
    result = LimitResult.model_validate(await limiter.limit({'key': key}))

    if not result.success:
        raise RateLimited


async def siteverify(
    secret: str,
    proof: str,
    remote_ip: str,
    command_id: UUID,
    *,
    endpoint: str = 'https://challenges.cloudflare.com/turnstile/v0/siteverify',
) -> bytes:
    """Fetch one bounded provider result with native cancellation and no retry."""
    from js import AbortSignal, Headers, fetch

    headers = Headers.new()
    headers.set('Content-Type', 'application/x-www-form-urlencoded')
    response = await fetch(
        endpoint,
        method='POST',
        headers=headers,
        body=urlencode(
            {
                'secret': secret,
                'response': proof,
                'remoteip': remote_ip,
                'idempotency_key': str(command_id),
            }
        ),
        redirect='manual',
        signal=AbortSignal.timeout(5000),
    )

    if not 200 <= response.status < 300:
        if not isinstance(response.body, JsNull):
            await response.body.cancel()

        raise ChallengeRejected

    try:
        return await read_body(response.body)
    except BodyRejected:
        raise ChallengeRejected from None


class BodyRejected(Exception):
    """The native body is absent or exceeds the API's 8 KiB limit."""


async def read_body(stream: ReadableStream | JsNull | None) -> bytes:
    if stream is None or isinstance(stream, JsNull):
        raise BodyRejected

    reader = stream.getReader()
    body = bytearray()

    try:
        while True:
            result = await reader.read()

            if result.done:
                return bytes(body)

            if result.value is None or len(body) + result.value.byteLength > 8192:
                raise BodyRejected

            body.extend(result.value.to_bytes())
    finally:
        await reader.cancel()
        reader.releaseLock()
