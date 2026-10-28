# Fix lost and duplicated jobs in jobq

Operators running several `jobq` workers against one queue in `/app` report that a job sometimes runs on two workers at once, that jobs held by a worker that crashed stay `running` forever, and that failed jobs are retried immediately however long their backoff should be. Enqueuing with the dedupe key of a job that already finished also does nothing.

Find and fix the causes so that the queue meets the contract in `README.md`, keeping its Python API, command line and database format, so existing queue files keep working. Add regression tests for each failure. Use only Python 3.14's standard library. Verification runs the code as an unprivileged user from a copy of `/app` that it may not be able to write, so keep the files the code or its tests create in temporary directories.
