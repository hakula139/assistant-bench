import asyncio
import gzip
import socket
from contextlib import suppress
from pathlib import Path

import filehost

from .server import BINARY, ServerCase, last_modified


class ExistingBehaviorTest(ServerCase):
    def test_full_responses_keep_their_fields(self) -> None:
        response, body = self.fetch('/data.bin')

        self.assertEqual(response.status, 200)
        self.assertEqual(body, BINARY)
        self.assertEqual(response.getheader('Content-Length'), '1000')
        self.assertEqual(response.getheader('Last-Modified'), last_modified())
        self.assertEqual(response.getheader('Cache-Control'), 'no-cache')
        self.assertIsNone(response.getheader('Content-Range'))

    def test_conditional_requests(self) -> None:
        tag = self.identity_tag('data.bin')
        cases = [
            ({'If-None-Match': tag}, 304),
            ({'If-None-Match': f'W/{tag}'}, 304),
            ({'If-None-Match': '"another-tag"'}, 200),
            ({'If-Match': '"another-tag"'}, 412),
            ({'If-Match': tag}, 200),
            ({'If-Modified-Since': last_modified()}, 304),
        ]
        for headers, status in cases:
            with self.subTest(headers=headers):
                response, _ = self.fetch('/data.bin', headers)

                self.assertEqual(response.status, status)

    def test_compression_follows_accept_encoding(self) -> None:
        identity = self.identity_tag('notes.txt')

        gzipped, body = self.fetch('/notes.txt', {'Accept-Encoding': 'br, gzip'})
        plain, plain_body = self.fetch('/notes.txt', {'Accept-Encoding': 'gzip;q=0'})

        self.assertEqual(gzipped.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(gzip.decompress(body), self.files['notes.txt'])
        self.assertEqual(gzipped.getheader('ETag'), f'{identity[:-1]}-gzip"')
        self.assertIsNone(plain.getheader('Content-Encoding'))
        self.assertEqual(plain_body, self.files['notes.txt'])

    def test_rejects_methods_and_hidden_paths(self) -> None:
        response, _ = self.fetch('/data.bin', {'Range': 'bytes=0-1'}, method='POST')
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader('Allow'), 'GET, HEAD')

        for path in ('/missing.bin', '/../data.bin', '/%2e%2e/data.bin'):
            with self.subTest(path=path):
                response, _ = self.fetch(path, {'Range': 'bytes=0-1'})

                self.assertEqual(response.status, 404)

    def test_http_1_0_closes_after_a_range_response(self) -> None:
        with socket.create_connection(('127.0.0.1', self.port), timeout=10) as client:
            client.sendall(b'GET /data.bin HTTP/1.0\r\nRange: bytes=0-4\r\n\r\n')
            received = b''
            while chunk := client.recv(65536):
                received += chunk

        head, _, body = received.partition(b'\r\n\r\n')
        self.assertIn(b'\r\nConnection: close', head)
        if head.startswith(b'HTTP/1.1 206 '):
            self.assertEqual(body, BINARY[:5])
        else:
            self.assertTrue(head.startswith(b'HTTP/1.1 200 '), head)
            self.assertEqual(body, BINARY)

    def test_readme_documents_range_requests(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text()

        self.assertIn('range', readme.lower())

    def test_python_api_serves_files(self) -> None:
        for name in ('FileBody', 'Headers', 'Request', 'Server', 'ServerConfig'):
            self.assertTrue(hasattr(filehost, name), f'filehost.{name} is missing')
        self.assertTrue(hasattr(filehost, 'handle'), 'filehost.handle is missing')
        config = filehost.ServerConfig(self.root, port=0)
        request = filehost.Request('GET', '/data.bin', 'HTTP/1.1', filehost.Headers())

        response = filehost.handle(request, config)
        received = asyncio.run(self.fetch_through_server(config))

        self.assertEqual(response.status, 200)
        self.assertEqual(read_body(response.body), BINARY)
        head, _, body = received.partition(b'\r\n\r\n')
        self.assertTrue(head.startswith(b'HTTP/1.1 200 '), head)
        self.assertEqual(body, BINARY)

    async def fetch_through_server(self, config: filehost.ServerConfig) -> bytes:
        server = filehost.Server(config)
        port = await server.start()
        serving = asyncio.create_task(server.serve_forever())
        try:
            async with asyncio.timeout(10):
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.write(
                    b'GET /data.bin HTTP/1.1\r\n'
                    b'Host: localhost\r\n'
                    b'Connection: close\r\n\r\n'
                )
                received = await reader.read()
                writer.close()
                await writer.wait_closed()
        finally:
            serving.cancel()
            with suppress(asyncio.CancelledError):
                await serving
        return received


def read_body(body: bytes | filehost.FileBody) -> bytes:
    if isinstance(body, bytes):
        return body
    with body.path.open('rb') as file:
        file.seek(body.offset)
        return file.read(body.length)
