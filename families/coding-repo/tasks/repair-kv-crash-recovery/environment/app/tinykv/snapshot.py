"""Snapshot files holding the store's contents up to a log sequence."""

import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .codec import FrameError, Op, Record, encode, frames
from .errors import CorruptSnapshot
from .fsutil import sync_file


MAGIC = b'TKVS'
HEADER = struct.Struct('>4sII')
TRAILER = struct.Struct('>I')
SNAPSHOT_NAME = 'snapshot.db'


@dataclass(frozen=True)
class Snapshot:
    covered: int
    """The last segment sequence whose records the snapshot includes."""

    items: dict[bytes, bytes]


def read_snapshot(path: Path) -> Snapshot:
    data = path.read_bytes()
    if len(data) < HEADER.size + TRAILER.size:
        raise CorruptSnapshot(path, 'truncated')

    body = data[: -TRAILER.size]
    (checksum,) = TRAILER.unpack_from(data, len(body))
    if zlib.crc32(body) != checksum:
        raise CorruptSnapshot(path, 'checksum mismatch')

    magic, covered, count = HEADER.unpack_from(body)
    if magic != MAGIC:
        raise CorruptSnapshot(path, 'not a tinykv snapshot')

    items: dict[bytes, bytes] = {}
    try:
        for frame in frames(body, HEADER.size):
            if frame.record.op is not Op.PUT:
                raise CorruptSnapshot(path, 'snapshot holds a delete record')
            items[frame.record.key] = frame.record.value
    except FrameError as error:
        raise CorruptSnapshot(path, str(error)) from error

    if len(items) != count:
        raise CorruptSnapshot(path, 'record count mismatch')

    return Snapshot(covered, items)


def write_snapshot(
    path: Path, items: Sequence[tuple[bytes, bytes]], covered: int, *, sync: bool
) -> None:
    """Write `items`, in order, as the snapshot covering segments to `covered`."""
    with path.open('wb') as file:
        header = HEADER.pack(MAGIC, covered, len(items))
        file.write(header)
        checksum = zlib.crc32(header)
        for key, value in items:
            frame = encode(Record(Op.PUT, key, value))
            file.write(frame)
            checksum = zlib.crc32(frame, checksum)

        file.write(TRAILER.pack(checksum))
        if sync:
            sync_file(file)
