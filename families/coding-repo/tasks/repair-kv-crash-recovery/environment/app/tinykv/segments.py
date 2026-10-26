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
    """Return the sequence number and path of every segment in `directory`."""
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob('wal-*.log')):
        match = _NAME.fullmatch(path.name)
        if match is not None:
            found.append((int(match[1]), path))

    return found


def read_segment(path: Path) -> Segment:
    data = path.read_bytes()
    if len(data) < HEADER.size:
        raise CorruptSegment(path, 0, 'incomplete header')

    magic, sequence = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise CorruptSegment(path, 0, 'not a tinykv segment')

    records: list[Record] = []
    end = HEADER.size
    try:
        for frame in frames(data, HEADER.size):
            records.append(frame.record)
            end = frame.end
    except FrameError:
        # An interrupted append leaves an incomplete final record.
        pass

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
        file = path.open('ab')
        return cls(file, segment.sequence, file.tell(), sync=sync)

    def append(self, data: bytes) -> None:
        self._file.write(data)
        if self._sync:
            sync_file(self._file)
        else:
            self._file.flush()

        self.size += len(data)

    def close(self) -> None:
        self._file.close()
