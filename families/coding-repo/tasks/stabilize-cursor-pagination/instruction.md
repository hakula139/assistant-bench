# Stabilize event-feed pagination under concurrent inserts

The event feed in `/app` continues pages with offset cursors, which can duplicate or skip events when new rows arrive between requests. Replace offset continuation with snapshot-bound keyset pagination through the existing repository, service and CLI.

Follow the public API, ordering, filter and cursor contract in `README.md`. A traversal must return every original matching event exactly once and exclude later inserts, including backdated inserts. Preserve meaningful errors, allow page-size changes and omit a next cursor on the final page.

Add focused regression tests for the changed behavior. Use only Python 3.14's standard library. Verification runs the code as an unprivileged user from a copy of `/app` that it may not be able to write, so keep the files the code or its tests create in temporary directories.
