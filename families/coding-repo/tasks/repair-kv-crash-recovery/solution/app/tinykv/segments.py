"""Write-ahead log segments: a header followed by framed records."""

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Self

from .codec import FrameError, Record, frames
from .errors import CorruptSegment
from .fsutil import sync_directory, sync_file


MAGIC = b'TKVL'
HEADER = struct.Struct('>4sI')

_NAME = re.compile(r'wal-([1-9][0-9]*)\.log')


@dataclass(frozen=True)
class Segment:
    sequence: int
    records: list[Record]
    end: int
    """Offset just past the last intact record."""


def segment_path(directory: Path, sequence: int) -> Path:
    return directory / f'wal-{sequence}.log'


def list_segments(directory: Path) -> list[tuple[int, Path]]:
    """Return the sequence number and path of every segment, in sequence order."""
    found: list[tuple[int, Path]] = []
    for path in directory.glob('wal-*.log'):
        match = _NAME.fullmatch(path.name)
        if match is not None:
            found.append((int(match[1]), path))

    return sorted(found)


def read_segment(path: Path, sequence: int, *, active: bool) -> Segment:
    """Read the segment named for `sequence`.

    A crash can interrupt only the active segment's last write, so its records
    end at the first incomplete one, and a torn header leaves it empty with
    `end` 0. Any damage to a sealed segment raises `CorruptSegment`.
    """
    data = path.read_bytes()
    header = HEADER.pack(MAGIC, sequence)
    if len(data) < HEADER.size:
        if active and header.startswith(data):
            return Segment(sequence, [], 0)
        raise CorruptSegment(path, 0, 'incomplete header')

    magic, found = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise CorruptSegment(path, 0, 'not a tinykv segment')
    if found != sequence:
        raise CorruptSegment(path, 0, 'sequence does not match the name')

    records: list[Record] = []
    end = HEADER.size
    try:
        for frame in frames(data, HEADER.size):
            records.append(frame.record)
            end = frame.end
    except FrameError as error:
        if not active:
            raise CorruptSegment(path, error.offset, error.reason) from error

    return Segment(sequence, records, end)


class SegmentWriter:
    """Append framed records to one segment file."""

    def __init__(self, file: BinaryIO, sequence: int, size: int, *, sync: bool) -> None:
        self.sequence = sequence
        self.size = size
        self._file = file
        self._sync = sync

    @classmethod
    def create(cls, path: Path, sequence: int, *, sync: bool) -> Self:
        file = path.open('xb')
        try:
            file.write(HEADER.pack(MAGIC, sequence))
            if sync:
                sync_file(file)
                sync_directory(path.parent)
            else:
                file.flush()
        except BaseException:
            file.close()
            raise

        return cls(file, sequence, HEADER.size, sync=sync)

    @classmethod
    def reopen(cls, path: Path, segment: Segment, *, sync: bool) -> Self:
        """Continue the active segment after dropping a crash's incomplete write."""
        file = path.open('r+b')
        try:
            file.truncate(segment.end)
            file.seek(segment.end)
            if segment.end == 0:
                file.write(HEADER.pack(MAGIC, segment.sequence))
            if sync:
                sync_file(file)
        except BaseException:
            file.close()
            raise

        return cls(file, segment.sequence, max(segment.end, HEADER.size), sync=sync)

    def append(self, data: bytes) -> None:
        self._file.write(data)
        if self._sync:
            sync_file(self._file)
        else:
            self._file.flush()

        self.size += len(data)

    def close(self) -> None:
        self._file.close()
