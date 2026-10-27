"""Drive a store in a child process that can crash at any file mutation.

`run DIRECTORY SEGMENT_BYTES OPS [CRASH_AT [tear]]` opens the store, applies
the JSON operations, and prints `done <index>` after each one returns. Every
file mutation the operations make is counted: creating or truncating a file,
writing to it, renaming, truncating or removing it. With `CRASH_AT`, the
process exits with status 86 just before that mutation, or with `tear`, after
writing only the first half of that write's bytes. Without it, the run prints
`mutation <kind>` for each one.

`recover DIRECTORY SEGMENT_BYTES` opens the store without any fault, prints
`entries <JSON>`, puts a probe entry, reopens the store, and prints them again.
"""

import builtins
import io
import json
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Self


CRASH_STATUS = 86
PROBE = (b'probe-after-recovery', b'still here')


class Injector:
    def __init__(self, crash_at: int | None, tear: bool) -> None:
        self.active = False
        self.count = 0
        self.crash_at = crash_at
        self.tear = tear

    def mutate(
        self,
        kind: str,
        partial: Callable[[Any], object] | None = None,
        data: Any = None,
    ) -> None:
        if not self.active:
            return

        self.count += 1
        if self.crash_at is None:
            print(f'mutation {kind}', flush=True)
        elif self.count == self.crash_at:
            if self.tear and partial is not None and len(data) > 1:
                partial(data[: len(data) // 2])
            os._exit(CRASH_STATUS)


class TrackedFile:
    """Count the writes and truncations made through a file object."""

    def __init__(self, file: Any, injector: Injector) -> None:
        self._file = file
        self._injector = injector

    def write(self, data: Any) -> int:
        def partial(part: Any) -> None:
            self._file.write(part)
            self._file.flush()

        self._injector.mutate('write', partial, data)
        return self._file.write(data)

    def writelines(self, lines: Iterable[Any]) -> None:
        for line in lines:
            self.write(line)

    def truncate(self, *args: Any) -> int:
        self._injector.mutate('truncate')
        return self._file.truncate(*args)

    def __enter__(self) -> Self:
        self._file.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._file.__exit__(*exc)

    def __iter__(self) -> Any:
        return iter(self._file)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)


def install(injector: Injector) -> None:
    """Count file mutations made through the `io`, `builtins` and `os` APIs."""
    original_open = io.open

    def tracked_open(file: Any, mode: str = 'r', *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, int) or not any(flag in mode for flag in 'wax+'):
            return original_open(file, mode, *args, **kwargs)

        if 'w' in mode or 'x' in mode or not os.path.exists(file):
            injector.mutate('create')
        return TrackedFile(original_open(file, mode, *args, **kwargs), injector)

    io.open = tracked_open
    builtins.open = tracked_open

    original_os_open = os.open

    def tracked_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_TRUNC or (flags & os.O_CREAT and not os.path.exists(path)):
            injector.mutate('create')
        return original_os_open(path, flags, *args, **kwargs)

    os.open = tracked_os_open

    original_write = os.write

    def tracked_write(fd: int, data: Any) -> int:
        injector.mutate('write', lambda part: original_write(fd, part), data)
        return original_write(fd, data)

    os.write = tracked_write

    for name in ('replace', 'rename', 'remove', 'unlink', 'truncate', 'ftruncate'):
        wrap(name, injector)


def wrap(name: str, injector: Injector) -> None:
    original = getattr(os, name)

    def tracked(*args: Any, **kwargs: Any) -> Any:
        injector.mutate(name)
        return original(*args, **kwargs)

    setattr(os, name, tracked)


def entries(store: Any) -> dict[str, str]:
    return {key.hex(): value.hex() for key, value in store.items()}


def run(
    directory: Path, segment_bytes: int, ops: list[list[str]], args: list[str]
) -> None:
    injector = Injector(int(args[0]) if args else None, args[1:] == ['tear'])
    install(injector)
    from tinykv import Store

    store = Store.open(directory, segment_bytes=segment_bytes)
    injector.active = True
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


def recover(directory: Path, segment_bytes: int) -> None:
    from tinykv import Store

    with Store.open(directory, segment_bytes=segment_bytes) as store:
        print(f'entries {json.dumps(entries(store))}', flush=True)
        store.put(*PROBE)

    with Store.open(directory, segment_bytes=segment_bytes) as store:
        print(f'entries {json.dumps(entries(store))}', flush=True)


def main() -> None:
    command, directory, segment_bytes, *rest = sys.argv[1:]
    if command == 'run':
        run(Path(directory), int(segment_bytes), json.loads(rest[0]), rest[1:])
    else:
        recover(Path(directory), int(segment_bytes))


if __name__ == '__main__':
    main()
