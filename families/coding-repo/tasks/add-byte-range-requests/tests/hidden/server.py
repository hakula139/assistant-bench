"""Run the candidate's server in a subprocess and talk to it over HTTP."""

import email.utils
import gzip
import http.client
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODIFIED = 1_700_000_000
BINARY = bytes((index * 7 + index // 256) % 256 for index in range(1000))
TEXT = ''.join(f'line {index:04d} of the served text file\n' for index in range(200))


class ServerCase(unittest.TestCase):
    """Serves a root holding `data.bin`, `notes.txt`, `empty.bin` and `large.bin`."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.files = {
            'data.bin': BINARY,
            'notes.txt': TEXT.encode(),
            'empty.bin': b'',
            'large.bin': os.urandom(3 * 1024 * 1024),
        }
        for name, content in self.files.items():
            path = self.root / name
            path.write_bytes(content)
            os.utime(path, (MODIFIED, MODIFIED))

        server = subprocess.Popen(
            [
                sys.executable,
                '-m',
                'filehost',
                'serve',
                str(self.root),
                '--port',
                '0',
                '--quiet',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(server.wait, 10)
        self.addCleanup(server.kill)
        assert server.stdout is not None
        self.addCleanup(server.stdout.close)

        line = server.stdout.readline()
        match = re.fullmatch(r'listening on http://[^:]+:(\d+)\n', line)
        if match is None:
            self.fail(f'the server printed {line!r} instead of its address')
        self.port = int(match.group(1))

    def connect(self) -> http.client.HTTPConnection:
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        self.addCleanup(connection.close)
        return connection

    def fetch(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        method: str = 'GET',
    ) -> tuple[http.client.HTTPResponse, bytes]:
        connection = self.connect()
        connection.request(method, path, headers=headers or {})
        try:
            response = connection.getresponse()
        except http.client.RemoteDisconnected:
            self.fail(f'the server closed the connection without answering {path}')
        return response, response.read()

    def identity_tag(self, name: str) -> str:
        response, _ = self.fetch(f'/{name}', method='HEAD')
        tag = response.getheader('ETag')
        assert tag is not None
        return tag

    def assert_partial(
        self,
        response: http.client.HTTPResponse,
        body: bytes,
        representation: bytes,
        first: int,
        last: int,
    ) -> None:
        self.assertEqual(response.status, 206)
        self.assertEqual(
            parse_content_range(response.getheader('Content-Range') or ''),
            (first, last, len(representation)),
        )
        self.assertEqual(body, representation[first : last + 1])
        self.assertEqual(response.getheader('Content-Length'), str(len(body)))


def last_modified() -> str:
    return email.utils.formatdate(MODIFIED, usegmt=True)


def gzip_body(content: bytes) -> bytes:
    return gzip.compress(content, compresslevel=6, mtime=0)


def parse_content_range(value: str) -> tuple[int, int, int] | None:
    """Read a `bytes FIRST-LAST/LENGTH` field, whose unit is case-insensitive."""
    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', value.strip(), re.IGNORECASE)
    if match is None:
        return None
    first, last, length = (int(group) for group in match.groups())
    return first, last, length


def is_multipart(content_type: str) -> bool:
    return content_type.strip().lower().startswith('multipart/')


def multipart_parts(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """Split a `multipart/byteranges` body into its parts' ranges and bytes.

    The media type and parameter name are case-insensitive, the boundary not.
    """
    match = re.fullmatch(
        r'multipart/byteranges;\s*boundary="?([^";]+)"?',
        content_type.strip(),
        re.IGNORECASE,
    )
    if match is None:
        raise AssertionError(f'unexpected Content-Type {content_type!r}')
    delimiter = b'--' + match.group(1).encode()

    start = body.find(delimiter)
    if start < 0:
        raise AssertionError('the body holds no boundary delimiter')

    parts: list[tuple[str, bytes]] = []
    for section in (b'\r\n' + body[start:]).split(b'\r\n' + delimiter)[1:]:
        if section.startswith(b'--'):
            break
        head, separator, data = section.removeprefix(b'\r\n').partition(b'\r\n\r\n')
        if not separator:
            raise AssertionError(f'a part has no header block: {section[:80]!r}')
        fields = {
            name.strip().lower(): value.strip()
            for name, _, value in (
                line.partition(':') for line in head.decode('latin-1').split('\r\n')
            )
        }
        parts.append((fields.get('content-range', ''), data))
    return parts
