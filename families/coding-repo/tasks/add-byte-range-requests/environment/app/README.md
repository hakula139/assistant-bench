# filehost

`filehost` serves the files under one directory over HTTP/1.1. It handles conditional requests, gzip compression and persistent connections, and it only reads the files it serves.

```bash
python -m filehost serve ROOT [--host 127.0.0.1] [--port 8000] [--quiet]
```

Once it listens, the command prints `listening on http://HOST:PORT` on standard output, where port 0 picks a free port. Access lines go to standard error unless `--quiet` is set.

## Requests

- `GET` and `HEAD` are supported. Other methods get `405 Method Not Allowed` with `Allow: GET, HEAD`. A `HEAD` response carries the same header fields as the `GET` response would, without the body.
- The target's path is percent-decoded and resolved under `ROOT`. A directory serves its `index.html`. Paths with a segment starting with `.`, and paths that resolve outside `ROOT`, get `404 Not Found`, like missing files.
- Requests may not carry `Transfer-Encoding`. A `Content-Length` body of up to 64 KiB is read and ignored.
- HTTP/1.1 connections stay open until the client sends `Connection: close` or stays idle for 5 seconds, and HTTP/1.0 ones close after one response unless the client asks for `keep-alive`.

## Responses

A file's response carries `Content-Type` from its extension, `Content-Length`, `Last-Modified`, `Cache-Control: no-cache` and a strong `ETag` derived from the file's size and modification time.

Text, JSON and SVG files of at least 256 bytes are compressible. Their responses carry `Vary: Accept-Encoding`, and when the request's `Accept-Encoding` gives `gzip` a nonzero weight, the server sends the gzip representation: `Content-Encoding: gzip`, the compressed bytes, and an entity tag with `-gzip` appended inside the quotes. Compression is reproducible, so a file always yields the same compressed bytes.

## Conditional requests

`If-Match`, `If-Unmodified-Since`, `If-None-Match` and `If-Modified-Since` are evaluated against the selected representation in the order RFC 9110 section 13.2.2 defines. `If-Match` uses strong comparison and `If-None-Match` weak comparison. A failed `If-Match` or `If-Unmodified-Since` gets `412 Precondition Failed`, and a matching `If-None-Match`, or an `If-Modified-Since` no older than the file, gets `304 Not Modified` with the validators but no body.

## Python API

`filehost.Server(ServerConfig(root, host=..., port=...))` serves from asyncio: `await server.start()` binds and returns the port, and `await server.serve_forever()` runs it. `filehost.handle(request, config)` maps one `Request` to a `Response`, whose body is bytes or a `FileBody` naming a byte span of a file.
