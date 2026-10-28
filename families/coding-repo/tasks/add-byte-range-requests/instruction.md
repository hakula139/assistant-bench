# Let filehost resume downloads

Clients of the `filehost` server in `/app` cannot resume an interrupted download or seek within a media file, because it always sends whole files. Add support for byte range requests as RFC 9110 defines them, including `If-Range`, so that ranges work together with the server's existing conditional requests, gzip compression, `HEAD` and persistent connections.

Keep the current behavior of requests without a range, and the server's Python API and command line. Document the range behavior in `README.md` and add tests for it. Use only Python 3.14's standard library. Verification runs the code as an unprivileged user from a copy of `/app` that it may not be able to write, so keep the files the code or its tests create in temporary directories.
