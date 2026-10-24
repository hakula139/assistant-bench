"""Bounded results for the private native Services."""

from typing import Literal

from assistant_bench_arena.models import Record
from assistant_bench_arena.transport import Rejection


class RpcError(Record):
    reason: Rejection | Literal['unavailable']


class RpcFailure(Record):
    error: RpcError


class RpcSuccess[T](Record):
    result: T


type RpcResult[T] = RpcSuccess[T] | RpcFailure
