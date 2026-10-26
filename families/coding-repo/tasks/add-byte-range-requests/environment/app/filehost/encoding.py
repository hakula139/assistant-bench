"""Gzip content coding, negotiated through `Accept-Encoding`."""

import gzip


COMPRESSIBLE_TYPES = frozenset(
    {
        'application/json',
        'image/svg+xml',
        'text/css',
        'text/html',
        'text/javascript',
        'text/plain',
    }
)


def compressible(content_type: str, size: int, min_bytes: int) -> bool:
    media_type = content_type.partition(';')[0].strip()
    return media_type in COMPRESSIBLE_TYPES and size >= min_bytes


def accepts_gzip(accept_encoding: str | None) -> bool:
    """Whether a request's `Accept-Encoding` gives gzip a nonzero weight."""
    if accept_encoding is None:
        return False

    weights: dict[str, float] = {}
    for item in accept_encoding.split(','):
        coding, *parameters = (part.strip() for part in item.split(';'))
        if not coding:
            continue

        weight = 1.0
        for parameter in parameters:
            name, _, value = parameter.partition('=')
            if name.strip().lower() == 'q':
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0.0
        weights[coding.lower()] = weight

    for coding in ('gzip', 'x-gzip', '*'):
        if coding in weights:
            return weights[coding] > 0
    return False


def compress(data: bytes) -> bytes:
    """Gzip `data` reproducibly, so every request gets the same bytes."""
    return gzip.compress(data, compresslevel=6, mtime=0)


def gzip_etag(etag: str) -> str:
    """Derive the gzip representation's entity tag from the file's own."""
    return f'{etag[:-1]}-gzip"'
