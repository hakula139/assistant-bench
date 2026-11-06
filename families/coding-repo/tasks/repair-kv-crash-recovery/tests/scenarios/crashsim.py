"""Drive a store in a scenario process that can crash at any file mutation.

`run` opens the store, prints `opened`, applies the operations, and prints
`done <index>` after each one returns. Each call that opening the store and the
operations make to change a file or directory is a mutation, numbered from 1:
creating or truncating a file as it opens, writing to it through a file object
or a descriptor, resizing it, and creating, renaming or removing an entry. A
write to a buffered file object counts when `write` is called, even if its bytes
stay in the buffer until a flush. Memory-mapped writes are not counted.

With `crash_at`, the process exits with status 86 in place of that mutation, so
neither it nor any later one happens, and bytes still buffered in a file object
are lost, as in a real crash. With `tear` as well, a write first passes the
first half of its bytes to the file, along with whatever its file object had
buffered. Without `crash_at`, the run prints `mutation <kind>` for each
mutation, with the kind `write` for every write, and `sync file` or `sync dir`
for each `fsync` or `fdatasync`. It also prints `sync file` once bytes reach a
descriptor opened with `O_DSYNC` or `O_SYNC`, through a write or a flush, and
both lines for each `os.sync`.
"""

import builtins
import fcntl
import io
import os
import stat
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Self


CRASH_STATUS = 86


class Injector:
    def __init__(self, crash_at: int | None, tear: bool) -> None:
        self.active = False
        self.count = 0
        self.crash_at = crash_at
        self.tear = tear

    def synced(self, *kinds: str) -> None:
        if self.active and self.crash_at is None:
            for kind in kinds:
                print(f'sync {kind}', flush=True)

    def reached(self, descriptor: int) -> None:
        """Report bytes just passed to `descriptor`, synced if it syncs each write."""
        if fcntl.fcntl(descriptor, fcntl.F_GETFL) & (os.O_DSYNC | os.O_SYNC):
            self.synced('file')

    def mutate(self, kind: str, torn: Callable[[], object] | None = None) -> None:
        """Count a mutation about to happen, crashing if it is `crash_at`.

        `torn` writes the first half of a write's bytes, for a torn crash.
        """
        if not self.active:
            return

        self.count += 1
        if self.crash_at is None:
            print(f'mutation {kind}', flush=True)
        elif self.count == self.crash_at:
            if self.tear and torn is not None:
                torn()
            os._exit(CRASH_STATUS)


def half(data: Any) -> Any:
    return data[: len(data) // 2]


def writes(mode: str) -> bool:
    return any(flag in mode for flag in 'wax+')


def creates(file: Any, mode: str) -> bool:
    """Tell whether opening `file` in a writing `mode` creates or truncates it."""
    if isinstance(file, int):
        return False
    return 'w' in mode or 'x' in mode or not os.path.exists(file)


class TrackedFile:
    """Count the writes and truncations made through a file object."""

    def __init__(self, file: Any, injector: Injector) -> None:
        self._file = file
        self._injector = injector
        self._unreported = False

    def write(self, data: Any) -> int:
        def torn() -> None:
            self._file.write(half(data))
            self._file.flush()

        self._injector.mutate('write', torn)
        written = self._file.write(data)
        self._unreported = True
        if isinstance(self._file, io.RawIOBase):
            self._report()
        return written

    def _report(self) -> None:
        """Report the bytes written since the last report as reaching the file."""
        if self._unreported:
            self._unreported = False
            self._injector.reached(self._file.fileno())

    def writelines(self, lines: Iterable[Any]) -> None:
        for line in lines:
            self.write(line)

    def truncate(self, *args: Any) -> int:
        self._injector.mutate('truncate')
        return self._file.truncate(*args)

    def flush(self) -> None:
        self._file.flush()
        self._report()

    def close(self) -> None:
        if not self._file.closed:
            self.flush()
        self._file.close()

    def __enter__(self) -> Self:
        self._file.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __iter__(self) -> Any:
        return iter(self._file)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)


class TrackedFileIO(io.FileIO):
    """Count the mutations made through an `io.FileIO` built directly."""

    injector: Injector

    def __init__(
        self, file: Any, mode: str = 'r', closefd: bool = True, opener: Any = None
    ) -> None:
        if writes(mode) and creates(file, mode):
            self.injector.mutate('create')
        super().__init__(file, mode, closefd, opener)

    def write(self, data: Any, /) -> int | None:
        self.injector.mutate('write', lambda: io.FileIO.write(self, half(data)))
        written = super().write(data)
        self.injector.reached(self.fileno())
        return written

    def truncate(self, size: int | None = None, /) -> int:
        self.injector.mutate('truncate')
        return super().truncate(size)


def install(injector: Injector) -> None:
    """Count file mutations made through the `io`, `builtins` and `os` APIs."""
    original_open = io.open

    def tracked_open(file: Any, mode: str = 'r', *args: Any, **kwargs: Any) -> Any:
        if not writes(mode):
            return original_open(file, mode, *args, **kwargs)

        if creates(file, mode):
            injector.mutate('create')
        return TrackedFile(original_open(file, mode, *args, **kwargs), injector)

    io.open = tracked_open
    builtins.open = tracked_open
    TrackedFileIO.injector = injector
    io.FileIO = TrackedFileIO

    original_os_open = os.open

    def tracked_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_TRUNC or (flags & os.O_CREAT and not os.path.exists(path)):
            injector.mutate('create')
        return original_os_open(path, flags, *args, **kwargs)

    os.open = tracked_os_open

    track_writes(injector)
    for name in ('sendfile', 'copy_file_range', 'splice'):
        if hasattr(os, name):
            track_copy(name, injector)

    names = (
        'truncate',
        'ftruncate',
        'posix_fallocate',
        'replace',
        'rename',
        'link',
        'symlink',
        'remove',
        'unlink',
        'mkdir',
        'rmdir',
    )
    for name in names:
        if hasattr(os, name):
            wrap(name, injector)
    for name in ('fsync', 'fdatasync'):
        if hasattr(os, name):
            track_sync(name, injector)
    track_sync_all(injector)


def track_writes(injector: Injector) -> None:
    """Count the writes made through descriptors, at their offset or a given one."""
    write, pwrite, writev, pwritev = os.write, os.pwrite, os.writev, os.pwritev

    def tracked_write(fd: int, data: Any) -> int:
        injector.mutate('write', lambda: write(fd, half(data)))
        written = write(fd, data)
        injector.reached(fd)
        return written

    def tracked_pwrite(fd: int, data: Any, offset: int) -> int:
        injector.mutate('write', lambda: pwrite(fd, half(data), offset))
        written = pwrite(fd, data, offset)
        injector.reached(fd)
        return written

    def tracked_writev(fd: int, buffers: Iterable[Any]) -> int:
        buffers = list(buffers)
        injector.mutate('write', lambda: write(fd, half(b''.join(buffers))))
        written = writev(fd, buffers)
        injector.reached(fd)
        return written

    def tracked_pwritev(
        fd: int, buffers: Iterable[Any], offset: int, *flags: int
    ) -> int:
        buffers = list(buffers)
        injector.mutate('write', lambda: pwrite(fd, half(b''.join(buffers)), offset))
        written = pwritev(fd, buffers, offset, *flags)
        injector.reached(fd)
        return written

    os.write = tracked_write
    os.pwrite = tracked_pwrite
    os.writev = tracked_writev
    os.pwritev = tracked_pwritev


def track_copy(name: str, injector: Injector) -> None:
    """Count a call that copies up to a byte count into a descriptor."""
    original = getattr(os, name)
    count_at = 3 if name == 'sendfile' else 2
    target_at = 0 if name == 'sendfile' else 1

    def tracked(*args: Any, **kwargs: Any) -> Any:
        torn = (*args[:count_at], args[count_at] // 2, *args[count_at + 1 :])
        injector.mutate('write', lambda: original(*torn, **kwargs))
        copied = original(*args, **kwargs)
        injector.reached(args[target_at])
        return copied

    setattr(os, name, tracked)


def wrap(name: str, injector: Injector) -> None:
    original = getattr(os, name)

    def tracked(*args: Any, **kwargs: Any) -> Any:
        injector.mutate(name)
        return original(*args, **kwargs)

    setattr(os, name, tracked)


def track_sync(name: str, injector: Injector) -> None:
    original = getattr(os, name)

    def tracked(fd: Any) -> None:
        descriptor = fd if isinstance(fd, int) else fd.fileno()
        mode = os.fstat(descriptor).st_mode
        injector.synced('dir' if stat.S_ISDIR(mode) else 'file')
        original(fd)

    setattr(os, name, tracked)


def track_sync_all(injector: Injector) -> None:
    original = os.sync

    def tracked() -> None:
        injector.synced('file', 'dir')
        original()

    os.sync = tracked


def run(
    directory: str,
    segment_bytes: int,
    ops: list[list[str]],
    crash_at: int | None,
    tear: bool,
) -> None:
    """Apply `ops`, whose operands are hex, to the store in `directory`."""
    injector = Injector(crash_at, tear)
    install(injector)
    from tinykv import Store

    injector.active = True
    store = Store.open(Path(directory), segment_bytes=segment_bytes)
    print('opened', flush=True)
    for index, (name, *operands) in enumerate(ops):
        values = [bytes.fromhex(operand) for operand in operands]
        if name == 'put':
            store.put(values[0], values[1])
        elif name == 'delete':
            store.delete(values[0])
        else:
            store.compact()
        print(f'done {index}', flush=True)

    injector.active = False
    store.close()
