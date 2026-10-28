# tinykv

`tinykv` is a small embedded key-value store for byte keys and values. It keeps the whole data set in memory and persists every change through a write-ahead log, so a store directory survives restarts and crashes.

## Python API

`tinykv` exports `Store`, `Options`, `StoreError` and its subclasses `CorruptSegment`, `CorruptSnapshot` and `StoreClosed`.

`Store.open(directory: Path, *, segment_bytes: int = 1048576, sync: bool = True) -> Store` opens the store in `directory`, creating it when needed. `segment_bytes` must be at least 64. A store is a context manager, and `close()` releases it. Every other method raises `StoreClosed` once the store is closed.

- `get(key: bytes) -> bytes | None` returns the value or `None`.
- `put(key: bytes, value: bytes) -> None` stores a value. Keys hold 1 to 65,535 bytes and values at most 16 MiB, and other sizes raise `ValueError`.
- `delete(key: bytes) -> bool` removes a key and returns whether it was present.
- `items(prefix: bytes = b'') -> list[tuple[bytes, bytes]]` returns the entries whose keys start with `prefix`, sorted by key, and `len(store)` counts every entry.
- `compact() -> None` folds the log into a new snapshot.

## Command line

`python -m tinykv DIRECTORY [--segment-bytes N] COMMAND` runs one command against a store with UTF-8 keys and values: `put KEY VALUE`, `get KEY`, `delete KEY`, `scan [PREFIX]` (one `KEY<TAB>VALUE` line per entry) or `compact`. A missing key and any store error exit with status 1 and a message on standard error.

## Storage

A store directory holds at most one snapshot, `snapshot.db`, and log segments named `wal-<sequence>.log`, where `<sequence>` is a decimal number starting at 1. Integers are big-endian.

- A **frame** holds one record: the CRC-32 of its body and the body's length as two 32-bit integers, then the body. The body is an operation byte (1 for put, 2 for delete), the key's length as a 16-bit integer, the key, and for a put the value.
- A **segment** starts with the magic `TKVL` and its sequence as a 32-bit integer, followed by frames in the order the operations happened.
- The **snapshot** starts with the magic `TKVS`, the sequence of the last segment it covers, and its number of entries, as two 32-bit integers. Put frames for every entry follow in key order, and a trailing CRC-32 covers every preceding byte.

Every `put` and `delete` appends its frame to the active segment, the one with the highest sequence, before it returns. When a frame would grow a nonempty active segment past `segment_bytes`, the store seals that segment and starts the next. `compact` seals the active segment, writes a snapshot covering every sealed segment, and removes the segments it covers. Opening a store loads the snapshot, then replays the segments after the ones it covers in sequence order.

## Durability

With `sync=True`, each operation's frame is flushed and synced to disk before the call returns. With `sync=False` it is flushed to the operating system only, which survives a process crash but not a power loss.

The process can crash at any point, including partway through writing a file, and the files keep whatever bytes reached them. After such a crash, `Store.open` succeeds and holds every operation that returned before the crash. The operation that was running is either fully applied or absent, and nothing else changes. `compact` never changes the store's contents.

Damage to the snapshot or a sealed segment, which no crash can cause, is reported, since the data it held cannot be recovered: `Store.open` raises `CorruptSnapshot` for a damaged snapshot and `CorruptSegment` for a sealed segment with a bad header or frame, including one cut short within a frame. A sealed segment cut exactly between frames reads as a shorter log, since the format records no segment length. A bad frame in the active segment reads as a crash's torn tail, so the store keeps the frames before it and drops the rest.
