"""Native fetch declarations used with the locked Python Worker SDK."""

from collections.abc import Awaitable
from typing import Literal, Protocol

from pyodide.ffi import JsBuffer, JsNull

class Env(Protocol):
    pass

class ExecutionContext(Protocol):
    pass

class AbortSignal:
    @staticmethod
    def timeout(delay: int, /) -> AbortSignal: ...

class Headers:
    @staticmethod
    def new() -> Headers: ...
    def get(self, name: str, /) -> str | JsNull: ...
    def set(self, name: str, value: str, /) -> None: ...

class Uint8Array(JsBuffer):
    byteLength: int

class ReadResult:
    done: bool
    value: Uint8Array | None

class ReadableStreamDefaultReader:
    def read(self) -> Awaitable[ReadResult]: ...
    def cancel(self) -> Awaitable[None]: ...
    def releaseLock(self) -> None: ...

class ReadableStream:
    def getReader(self) -> ReadableStreamDefaultReader: ...
    def cancel(self) -> Awaitable[None]: ...

class Request:
    method: str
    url: str
    headers: Headers
    body: ReadableStream | JsNull

class Response:
    @staticmethod
    def new(body: str | ReadableStream | None, init: Response, /) -> Response: ...
    status: int
    headers: Headers
    body: ReadableStream | JsNull
    def text(self) -> Awaitable[str]: ...

def fetch(
    input: str,
    /,
    *,
    method: str,
    headers: Headers,
    body: str,
    redirect: Literal['follow', 'manual'],
    signal: AbortSignal,
) -> Awaitable[Response]: ...
