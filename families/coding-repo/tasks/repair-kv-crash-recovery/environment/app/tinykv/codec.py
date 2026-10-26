"""Checksummed frames that hold one put or delete record each."""

import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum


FRAME = struct.Struct('>II')
KEY_LENGTH = struct.Struct('>H')
MAX_KEY_BYTES = 0xFFFF
MAX_VALUE_BYTES = 16 * 1024 * 1024


class Op(IntEnum):
    PUT = 1
    DELETE = 2


@dataclass(frozen=True)
class Record:
    op: Op
    key: bytes
    value: bytes = b''


@dataclass(frozen=True)
class Frame:
    start: int
    end: int
    record: Record


class FrameError(Exception):
    """The bytes at `offset` do not hold a complete, intact frame."""

    def __init__(self, offset: int, reason: str) -> None:
        self.offset = offset
        self.reason = reason
        super().__init__(f'byte {offset}: {reason}')


def encode(record: Record) -> bytes:
    """Frame a record as its body's CRC-32 and length, then the body."""
    body = b''.join(
        (
            bytes((record.op,)),
            KEY_LENGTH.pack(len(record.key)),
            record.key,
            record.value,
        )
    )
    return FRAME.pack(zlib.crc32(body), len(body)) + body


def frames(data: bytes, start: int) -> Iterator[Frame]:
    """Yield each frame from `start` to the end, raising at the first bad one."""
    offset = start
    while offset < len(data):
        if len(data) - offset < FRAME.size:
            raise FrameError(offset, 'incomplete frame header')

        checksum, length = FRAME.unpack_from(data, offset)
        body_start = offset + FRAME.size
        body = data[body_start : body_start + length]
        if len(body) < length:
            raise FrameError(offset, 'incomplete frame body')
        if zlib.crc32(body) != checksum:
            raise FrameError(offset, 'checksum mismatch')

        end = body_start + length
        yield Frame(offset, end, _decode(body, offset))
        offset = end


def _decode(body: bytes, offset: int) -> Record:
    if len(body) < 1 + KEY_LENGTH.size:
        raise FrameError(offset, 'record too short')

    try:
        op = Op(body[0])
    except ValueError:
        raise FrameError(offset, f'unknown operation {body[0]}') from None

    (key_length,) = KEY_LENGTH.unpack_from(body, 1)
    key_end = 1 + KEY_LENGTH.size + key_length
    key = body[1 + KEY_LENGTH.size : key_end]
    if not key or len(key) != key_length:
        raise FrameError(offset, 'bad key length')

    value = body[key_end:]
    if op is Op.DELETE and value:
        raise FrameError(offset, 'delete record with a value')

    return Record(op, key, value)
