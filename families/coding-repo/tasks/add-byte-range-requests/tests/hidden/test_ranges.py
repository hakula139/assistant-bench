import email.utils

from .server import (
    BINARY,
    MODIFIED,
    ServerCase,
    gzip_body,
    last_modified,
    multipart_parts,
)


class RangeTest(ServerCase):
    def test_full_responses_advertise_byte_ranges(self) -> None:
        for method in ('GET', 'HEAD'):
            with self.subTest(method=method):
                response, _ = self.fetch('/data.bin', method=method)

                self.assertEqual(response.status, 200)
                self.assertEqual(
                    (response.getheader('Accept-Ranges') or '').lower(), 'bytes'
                )

    def test_single_ranges(self) -> None:
        cases = [
            ('bytes=0-0', 0, 0),
            ('bytes=0-99', 0, 99),
            ('bytes=100-', 100, 999),
            ('bytes=999-999', 999, 999),
            ('bytes=990-2000', 990, 999),
            ('bytes=-1', 999, 999),
            ('bytes=-200', 800, 999),
            ('bytes=-5000', 0, 999),
            ('Bytes=10-19', 10, 19),
            ('bytes=0-18446744073709551616', 0, 999),
        ]
        tag = self.identity_tag('data.bin')
        for field, first, last in cases:
            with self.subTest(range=field):
                response, body = self.fetch('/data.bin', {'Range': field})

                self.assert_partial(response, body, BINARY, first, last)
                self.assertEqual(response.getheader('ETag'), tag)
                self.assertEqual(response.getheader('Last-Modified'), last_modified())
                self.assertEqual(
                    response.getheader('Content-Type'), 'application/octet-stream'
                )

    def test_unsatisfiable_ranges(self) -> None:
        cases = [
            ('data.bin', 'bytes=1000-', 1000),
            ('data.bin', 'bytes=5000-6000', 1000),
            ('data.bin', 'bytes=-0', 1000),
            ('data.bin', 'bytes=1000-1000, 2000-', 1000),
            ('empty.bin', 'bytes=0-0', 0),
        ]
        for name, field, length in cases:
            with self.subTest(file=name, range=field):
                response, _ = self.fetch(f'/{name}', {'Range': field})

                self.assertEqual(response.status, 416)
                self.assertEqual(
                    response.getheader('Content-Range'), f'bytes */{length}'
                )

    def test_ignores_invalid_or_foreign_ranges(self) -> None:
        for field in (
            'bytes=abc',
            'bytes=5-2',
            'items=0-1',
            'bytes=',
            'bytes=1-2-3',
            'bytes=0-1;q',
        ):
            with self.subTest(range=field):
                response, body = self.fetch('/data.bin', {'Range': field})

                self.assertEqual(response.status, 200)
                self.assertEqual(body, BINARY)
                self.assertIsNone(response.getheader('Content-Range'))

    def test_several_ranges_send_exactly_the_selected_bytes(self) -> None:
        cases = [
            ('bytes=0-9,20-29', set(range(10)) | set(range(20, 30))),
            ('bytes=0-9, 5-14, 990-', set(range(15)) | set(range(990, 1000))),
            ('bytes=0-9,5000-', set(range(10))),
            ('bytes=-10,0-0', {0} | set(range(990, 1000))),
            ('bytes=0-1,,4-5', {0, 1, 4, 5}),
        ]
        for field, selected in cases:
            with self.subTest(range=field):
                response, body = self.fetch('/data.bin', {'Range': field})

                if response.status == 200:
                    self.assertEqual(body, BINARY)
                    continue
                self.assertEqual(response.status, 206)
                content_type = response.getheader('Content-Type') or ''
                if content_type.startswith('multipart/'):
                    parts = multipart_parts(content_type, body)
                else:
                    parts = [(response.getheader('Content-Range') or '', body)]
                covered: set[int] = set()
                for content_range, data in parts:
                    first, last = self.parse_content_range(content_range)
                    self.assertEqual(data, BINARY[first : last + 1], content_range)
                    covered.update(range(first, last + 1))
                self.assertEqual(covered, selected)

    def parse_content_range(self, value: str) -> tuple[int, int]:
        unit, _, rest = value.partition(' ')
        span, _, length = rest.partition('/')
        first, _, last = span.partition('-')
        self.assertEqual((unit, length), ('bytes', '1000'), value)
        return int(first), int(last)

    def test_if_range_gates_the_ranges(self) -> None:
        tag = self.identity_tag('data.bin')
        earlier = email.utils.formatdate(MODIFIED - 60, usegmt=True)
        later = email.utils.formatdate(MODIFIED + 60, usegmt=True)
        cases = [
            (tag, True),
            ('"another-tag"', False),
            (f'W/{tag}', False),
            (last_modified(), True),
            (earlier, False),
            (later, False),
        ]
        for if_range, partial in cases:
            with self.subTest(if_range=if_range):
                response, body = self.fetch(
                    '/data.bin', {'Range': 'bytes=10-19', 'If-Range': if_range}
                )

                if partial:
                    self.assert_partial(response, body, BINARY, 10, 19)
                else:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(body, BINARY)

    def test_preconditions_take_precedence_over_ranges(self) -> None:
        tag = self.identity_tag('data.bin')
        old = email.utils.formatdate(MODIFIED - 3600, usegmt=True)
        cases = [
            ({'If-None-Match': tag}, 304),
            ({'If-Modified-Since': last_modified()}, 304),
            ({'If-Match': '"another-tag"'}, 412),
            ({'If-Unmodified-Since': old}, 412),
        ]
        for headers, status in cases:
            with self.subTest(headers=headers):
                response, body = self.fetch(
                    '/data.bin', {'Range': 'bytes=0-9', **headers}
                )

                self.assertEqual(response.status, status)
                self.assertIsNone(response.getheader('Content-Range'))
                if status == 304:
                    self.assertEqual(body, b'')

        response, body = self.fetch(
            '/data.bin', {'Range': 'bytes=0-9', 'If-Match': tag}
        )
        self.assert_partial(response, body, BINARY, 0, 9)

    def test_head_ignores_ranges(self) -> None:
        response, body = self.fetch('/data.bin', {'Range': 'bytes=0-9'}, method='HEAD')

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Length'), '1000')
        self.assertIsNone(response.getheader('Content-Range'))
        self.assertEqual(body, b'')

    def test_ranges_of_compressed_responses_stay_consistent(self) -> None:
        text = self.files['notes.txt']
        identity = self.identity_tag('notes.txt')
        compressed = f'{identity[:-1]}-gzip"'
        for field, first, last in (('bytes=0-99', 0, 99), ('bytes=-50', None, None)):
            with self.subTest(range=field):
                response, body = self.fetch(
                    '/notes.txt', {'Range': field, 'Accept-Encoding': 'gzip'}
                )

                gzipped = response.getheader('Content-Encoding') == 'gzip'
                representation = gzip_body(text) if gzipped else text
                self.assertEqual(
                    response.getheader('ETag'), compressed if gzipped else identity
                )
                self.assertEqual(response.getheader('Vary'), 'Accept-Encoding')
                if response.status == 200:
                    self.assertEqual(body, representation)
                    continue
                if first is None or last is None:
                    first, last = len(representation) - 50, len(representation) - 1
                self.assert_partial(response, body, representation, first, last)

    def test_if_range_compares_the_selected_representation(self) -> None:
        text = self.files['notes.txt']
        identity = self.identity_tag('notes.txt')
        for tag in (identity, f'{identity[:-1]}-gzip"'):
            with self.subTest(if_range=tag):
                response, body = self.fetch(
                    '/notes.txt',
                    {'Range': 'bytes=0-99', 'If-Range': tag, 'Accept-Encoding': 'gzip'},
                )

                gzipped = response.getheader('Content-Encoding') == 'gzip'
                representation = gzip_body(text) if gzipped else text
                if response.status == 200:
                    self.assertEqual(body, representation)
                    continue
                self.assertEqual(response.getheader('ETag'), tag)
                self.assert_partial(response, body, representation, 0, 99)

    def test_large_files_send_only_the_range(self) -> None:
        content = self.files['large.bin']
        for field, first, last in (
            ('bytes=1048576-2097151', 1048576, 2097151),
            ('bytes=-10', len(content) - 10, len(content) - 1),
        ):
            with self.subTest(range=field):
                response, body = self.fetch('/large.bin', {'Range': field})

                self.assert_partial(response, body, content, first, last)

    def test_connections_stay_usable_across_range_responses(self) -> None:
        connection = self.connect()
        exchanges: list[tuple[dict[str, str], int, bytes | None]] = [
            ({'Range': 'bytes=0-9'}, 206, BINARY[:10]),
            ({'Range': 'bytes=5000-'}, 416, None),
            ({}, 200, BINARY),
            ({'Range': 'bytes=-3'}, 206, BINARY[-3:]),
        ]
        for headers, status, expected in exchanges:
            connection.request('GET', '/data.bin', headers=headers)
            response = connection.getresponse()
            body = response.read()

            self.assertEqual(response.status, status, headers)
            self.assertEqual(response.getheader('Connection'), 'keep-alive', headers)
            if expected is not None:
                self.assertEqual(body, expected, headers)
