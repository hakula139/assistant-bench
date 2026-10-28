import gzip
import http.client
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / 'notes.txt').write_text('hello, filehost\n' * 40)
        (self.root / 'image.png').write_bytes(bytes(range(256)) * 4)
        (self.root / '.secret').write_text('hidden')

        server = subprocess.Popen(
            [sys.executable, '-m', 'filehost', 'serve', self.root, '--port', '0'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(server.wait, 5)
        self.addCleanup(server.terminate)
        assert server.stdout is not None
        self.addCleanup(server.stdout.close)
        self.port = int(server.stdout.readline().rpartition(':')[2])

    def connect(self) -> http.client.HTTPConnection:
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(connection.close)
        return connection

    def fetch(
        self, path: str, headers: dict[str, str] | None = None, method: str = 'GET'
    ) -> tuple[http.client.HTTPResponse, bytes]:
        connection = self.connect()
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response, response.read()

    def test_serves_files_with_validators(self) -> None:
        response, body = self.fetch('/image.png')

        self.assertEqual(response.status, 200)
        self.assertEqual(body, bytes(range(256)) * 4)
        self.assertEqual(response.getheader('Content-Type'), 'image/png')
        self.assertIsNotNone(response.getheader('ETag'))
        self.assertIsNotNone(response.getheader('Last-Modified'))

    def test_hides_dot_files_and_missing_paths(self) -> None:
        for path in ('/.secret', '/missing.txt', '/../etc/passwd'):
            with self.subTest(path=path):
                response, _ = self.fetch(path)

                self.assertEqual(response.status, 404)

    def test_matching_entity_tag_is_not_modified(self) -> None:
        first, _ = self.fetch('/image.png')

        response, body = self.fetch(
            '/image.png', {'If-None-Match': first.getheader('ETag') or ''}
        )

        self.assertEqual(response.status, 304)
        self.assertEqual(body, b'')

    def test_compresses_text_on_request(self) -> None:
        response, body = self.fetch('/notes.txt', {'Accept-Encoding': 'gzip'})

        self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(gzip.decompress(body), (self.root / 'notes.txt').read_bytes())
        self.assertEqual(response.getheader('Vary'), 'Accept-Encoding')

    def test_head_matches_get_without_a_body(self) -> None:
        get, _ = self.fetch('/image.png')

        head, body = self.fetch('/image.png', method='HEAD')

        self.assertEqual(body, b'')
        self.assertEqual(
            head.getheader('Content-Length'), get.getheader('Content-Length')
        )

    def test_rejects_other_methods(self) -> None:
        response, _ = self.fetch('/image.png', method='DELETE')

        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader('Allow'), 'GET, HEAD')

    def test_keeps_connections_alive(self) -> None:
        connection = self.connect()

        for _ in range(3):
            connection.request('GET', '/image.png')
            response = connection.getresponse()
            response.read()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader('Connection'), 'keep-alive')
