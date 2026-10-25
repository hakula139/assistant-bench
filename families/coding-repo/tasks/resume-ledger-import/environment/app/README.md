# Ledger import

Run `python -m ledgerload import --db ledger.db --file entries.csv --source-id upload-1 --batch-size 100`. The result is one JSON object with `processed`, `inserted` and `checkpoint`. Run `python -m ledgerload report --db ledger.db` for `{"rows": N, "total_cents": M}`. Failures exit nonzero with a diagnostic on stderr.

## Python API

`ledgerload` exports `import_file(db: Path, csv_path: Path, *, source_id: str, batch_size: int = 100) -> ImportResult`, `Store`, `LedgerRow`, `ImportResult`, `ImportError` and `ConflictError`.

`ImportResult.processed` counts logical data records consumed by this invocation, including duplicates. `inserted` counts newly committed ledger rows. `checkpoint` is the absolute logical record count committed for the source. `LedgerRow` has `external_id: str`, `amount_cents: int` and `memo: str`.

A CSV file uses UTF-8 and exactly the headers `external_id`, `amount` and `memo`, in any order. Standard CSV quoting permits commas and newlines inside fields. Empty physical rows are skipped. A quoted newline remains part of one logical record. IDs must be nonempty, while an empty memo is valid. Amounts use an optional ASCII minus, one or more ASCII digits, a dot and two digits. Individual integer-cent values must fit signed 64-bit SQLite integers. Report totals remain exact even when their sum exceeds that range.

Invalid rows raise `ImportError`, a `ValueError` subclass with a one-based `record_index: int`. Index zero identifies file-level errors, including a header, encoding or changed-source error. A conflicting external ID raises `ConflictError`, an `ImportError` subclass with the same record index. Nonempty source IDs and positive integer batch sizes are required. Invalid batch sizes raise `ValueError` before opening the database.

## Transactions and retries

A source ID binds the SHA-256 of the full input bytes. The initial source registration is durable before its first attempted batch, even if that batch fails. Reusing the source ID with changed bytes fails before ledger or checkpoint changes.

Every batch commits its new rows and checkpoint together. A failed batch changes neither, while previous batches remain committed. Identical external ID, cents and memo combinations are no-ops, including duplicates within one batch. A different payload is a conflict. Retry resumes after the committed checkpoint, counting skipped duplicate records in that checkpoint.

## Public storage

`Store(path: Path)` owns `connection: sqlite3.Connection` and supports a context manager that closes it. Its read methods are `rows() -> list[LedgerRow]`, `checkpoint(source_id: str) -> int`, `source_digest(source_id: str) -> str | None` and `report() -> dict[str, int]`. Unknown sources have checkpoint zero and no digest. The connection and tables are public so callers can install ordinary SQLite triggers.

`schema.sql` defines:

- `ledger(external_id TEXT PRIMARY KEY, amount_cents INTEGER NOT NULL, memo TEXT NOT NULL)`.
- `sources(source_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, checkpoint INTEGER NOT NULL DEFAULT 0 CHECK (checkpoint >= 0))`.

A trigger on `UPDATE OF checkpoint ON sources` may abort an update. That failure must roll back the same batch's ledger rows.
