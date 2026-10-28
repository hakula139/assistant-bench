"""A durable key-value store over a snapshot and write-ahead log segments."""

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from .codec import MAX_KEY_BYTES, MAX_VALUE_BYTES, Op, Record, encode
from .errors import CorruptSegment, StoreClosed
from .segments import (
    HEADER,
    Segment,
    SegmentWriter,
    list_segments,
    read_segment,
    segment_path,
)
from .snapshot import SNAPSHOT_NAME, Snapshot, read_snapshot, write_snapshot


DEFAULT_SEGMENT_BYTES = 1024 * 1024
MIN_SEGMENT_BYTES = 64


@dataclass(frozen=True)
class Options:
    segment_bytes: int = DEFAULT_SEGMENT_BYTES
    sync: bool = True


class Store:
    """Byte keys and values kept in memory and persisted through the log.

    Open a store with `Store.open`, which replays the snapshot and segments.
    """

    def __init__(
        self,
        directory: Path,
        options: Options,
        data: dict[bytes, bytes],
        writer: SegmentWriter,
    ) -> None:
        self.directory = directory
        self.options = options
        self._data = data
        self._writer: SegmentWriter | None = writer

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        segment_bytes: int = DEFAULT_SEGMENT_BYTES,
        sync: bool = True,
    ) -> Self:
        if segment_bytes < MIN_SEGMENT_BYTES:
            raise ValueError(f'segment_bytes must be at least {MIN_SEGMENT_BYTES}.')

        directory.mkdir(parents=True, exist_ok=True)
        snapshot_path = directory / SNAPSHOT_NAME
        snapshot = (
            read_snapshot(snapshot_path) if snapshot_path.exists() else Snapshot(0, {})
        )
        data = dict(snapshot.items)

        last: tuple[Path, Segment] | None = None
        for sequence, path in list_segments(directory):
            if sequence <= snapshot.covered:
                continue

            segment = read_segment(path)
            if segment.sequence != sequence:
                raise CorruptSegment(path, 0, 'sequence does not match the name')

            for record in segment.records:
                _apply(data, record)
            last = path, segment

        if last is None:
            sequence = snapshot.covered + 1
            writer = SegmentWriter.create(
                segment_path(directory, sequence), sequence, sync=sync
            )
        else:
            writer = SegmentWriter.reopen(*last, sync=sync)

        return cls(directory, Options(segment_bytes, sync), data, writer)

    def get(self, key: bytes) -> bytes | None:
        self._active()
        return self._data.get(key)

    def items(self, prefix: bytes = b'') -> list[tuple[bytes, bytes]]:
        """Return the entries whose keys start with `prefix`, sorted by key."""
        self._active()
        return sorted(
            (key, value) for key, value in self._data.items() if key.startswith(prefix)
        )

    def __len__(self) -> int:
        self._active()
        return len(self._data)

    def put(self, key: bytes, value: bytes) -> None:
        if not 1 <= len(key) <= MAX_KEY_BYTES:
            raise ValueError(f'Keys must hold 1 to {MAX_KEY_BYTES} bytes.')
        if len(value) > MAX_VALUE_BYTES:
            raise ValueError(f'Values must hold at most {MAX_VALUE_BYTES} bytes.')

        self._append(Record(Op.PUT, key, value))
        self._data[key] = value

    def delete(self, key: bytes) -> bool:
        """Remove `key`, returning whether it was present."""
        self._active()
        if key not in self._data:
            return False

        self._append(Record(Op.DELETE, key))
        del self._data[key]
        return True

    def compact(self) -> None:
        """Fold every sealed segment into a new snapshot and remove them."""
        self._rotate()
        covered = self._active().sequence - 1
        write_snapshot(
            self.directory / SNAPSHOT_NAME,
            sorted(self._data.items()),
            covered,
            sync=self.options.sync,
        )

        for sequence, path in list_segments(self.directory):
            if sequence <= covered:
                path.unlink()

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _active(self) -> SegmentWriter:
        if self._writer is None:
            raise StoreClosed()

        return self._writer

    def _append(self, record: Record) -> None:
        frame = encode(record)
        writer = self._active()
        if (
            writer.size > HEADER.size
            and writer.size + len(frame) > self.options.segment_bytes
        ):
            writer = self._rotate()

        writer.append(frame)

    def _rotate(self) -> SegmentWriter:
        """Seal the active segment and start the next one."""
        writer = self._active()
        writer.close()
        sequence = writer.sequence + 1
        self._writer = SegmentWriter.create(
            segment_path(self.directory, sequence), sequence, sync=self.options.sync
        )
        return self._writer


def _apply(data: dict[bytes, bytes], record: Record) -> None:
    if record.op is Op.PUT:
        data[record.key] = record.value
    else:
        data.pop(record.key, None)
