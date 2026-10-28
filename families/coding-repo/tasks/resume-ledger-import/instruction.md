# Make ledger imports atomic and resumable

The application in `/app` imports CSV rows into a persistent SQLite ledger. Its current row-at-a-time writes can leave rows committed without the matching checkpoint, and it reprocesses records after a restart.

Implement the transaction and retry contract in `README.md` across the importer, storage and CLI. Commit one batch's new rows and logical record checkpoint in one transaction. Bind each source ID to its original bytes, preserve exact integer cents and standard CSV records, and handle duplicate and conflicting external IDs.

Keep the documented Python exports, CLI JSON shapes and public schema usable. Add focused regression tests for the behavior you change. Use only Python 3.14's standard library. Verification runs the code as an unprivileged user from a copy of `/app` that it may not be able to write, so keep the files the code or its tests create in temporary directories.
