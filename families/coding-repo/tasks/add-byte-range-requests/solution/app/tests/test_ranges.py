import gzip
import http.client
import os
import subprocess
import sys
import tempfile
import unittest
from email.utils import formatdate
from pathlib import Path


MODIFIED = 1_700_000_000
DATA = bytes(range(256)) * 4
TEXT = b'a line of compressible text\n' * 50


class RangeTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, content in (('data.bin', DATA), ('notes.txt', TEXT)):
            (root / name).write_bytes(content)
            os.utime(root / name, (MODIFIED, MODIFIED))

        server = subprocess.Popen(
            [sys.executable, '-m', 'filehost', 'serve', root, '--port', '0', '--quiet'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(server.wait, 5)
        self.addCleanup(server.terminate)
        assert server.stdout is not None
        self.addCleanup(server.stdout.close)
        self.port = int(server.stdout.readline().rpartition(':')[2])

    def fetch(
        self, path: str, headers: dict[str, str], method: str = 'GET'
    ) -> tuple[http.client.HTTPResponse, bytes]:
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response, response.read()

    def test_single_ranges_select_bytes(self) -> None:
        cases = [
            ('bytes=0-9', 0, 9),
            ('bytes=1000-5000', 1000, 1023),
            ('bytes=-24', 1000, 1023),
            ('Bytes=-5000', 0, 1023),
            ('bytes=0-' + '9' * 5000, 0, 1023),
            ('bytes=-' + '9' * 5000, 0, 1023),
        ]
        for field, first, last in cases:
            with self.subTest(range=field):
                response, body = self.fetch('/data.bin', {'Range': field})

                self.assertEqual(response.status, 206)
                self.assertEqual(
                    response.getheader('Content-Range'), f'bytes {first}-{last}/1024'
                )
                self.assertEqual(body, DATA[first : last + 1])

    def test_unsatisfiable_and_invalid_ranges(self) -> None:
        cases = [
            ('bytes=1024-', 416),
            ('bytes=-0', 416),
            ('bytes=' + '9' * 5000 + '-', 416),
            ('bytes=9-1', 200),
            ('bytes=' + '9' * 5000 + '-9', 200),
            ('lines=0-1', 200),
        ]
        for field, status in cases:
            with self.subTest(range=field):
                response, body = self.fetch('/data.bin', {'Range': field})

                self.assertEqual(response.status, status)
                if status == 416:
                    self.assertEqual(
                        response.getheader('Content-Range'), 'bytes */1024'
                    )
                else:
                    self.assertEqual(body, DATA)

    def test_several_ranges_are_merged_into_parts(self) -> None:
        response, body = self.fetch('/data.bin', {'Range': 'bytes=0-1,1-3,10-11'})

        self.assertEqual(response.status, 206)
        content_type = response.getheader('Content-Type') or ''
        boundary = content_type.partition('boundary=')[2].encode()
        self.assertTrue(content_type.startswith('multipart/byteranges'))
        self.assertEqual(body.count(b'--' + boundary), 3)
        self.assertIn(b'Content-Range: bytes 0-3/1024\r\n\r\n' + DATA[0:4], body)
        self.assertIn(b'Content-Range: bytes 10-11/1024\r\n\r\n' + DATA[10:12], body)

    def test_if_range_needs_a_strong_validator(self) -> None:
        head, _ = self.fetch('/data.bin', {}, method='HEAD')
        tag = head.getheader('ETag') or ''
        cases = [
            (tag, 206),
            (f'W/{tag}', 200),
            (formatdate(MODIFIED, usegmt=True), 200),
        ]
        for if_range, status in cases:
            with self.subTest(if_range=if_range):
                response, _ = self.fetch(
                    '/data.bin', {'Range': 'bytes=0-0', 'If-Range': if_range}
                )

                self.assertEqual(response.status, status)

    def test_preconditions_and_head_come_first(self) -> None:
        head, _ = self.fetch('/data.bin', {}, method='HEAD')
        tag = head.getheader('ETag') or ''

        not_modified, _ = self.fetch(
            '/data.bin', {'Range': 'bytes=0-0', 'If-None-Match': tag}
        )
        ignored, body = self.fetch('/data.bin', {'Range': 'bytes=0-0'}, method='HEAD')

        self.assertEqual(not_modified.status, 304)
        self.assertEqual(ignored.status, 200)
        self.assertEqual(ignored.getheader('Content-Length'), '1024')
        self.assertEqual(body, b'')

    def test_ranges_of_gzip_responses_count_compressed_bytes(self) -> None:
        compressed = gzip.compress(TEXT, compresslevel=6, mtime=0)

        response, body = self.fetch(
            '/notes.txt', {'Range': 'bytes=-10', 'Accept-Encoding': 'gzip'}
        )

        self.assertEqual(response.status, 206)
        self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(
            response.getheader('Content-Range'),
            f'bytes {len(compressed) - 10}-{len(compressed) - 1}/{len(compressed)}',
        )
        self.assertEqual(body, compressed[-10:])
        self.assertTrue((response.getheader('ETag') or '').endswith('-gzip"'))
