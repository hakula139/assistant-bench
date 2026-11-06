import json
import unittest
from typing import Any

import sandbox


def event(identifier: str, occurred_at: int, tag: str) -> dict[str, Any]:
    return {'id': identifier, 'occurred_at': occurred_at, 'tag': tag}


class PageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = str(sandbox.scratch() / 'feed.db')

    def append(self, events: list[dict[str, Any]]) -> None:
        sandbox.observe('feed', 'append', path=self.db, events=events)

    def read(self, **args: Any) -> dict[str, Any]:
        """Return the scenario's page, naming the error class it raised, if any."""
        return sandbox.observe('feed', 'page', path=self.db, **args)

    def page(self, **args: Any) -> dict[str, Any]:
        result = self.read(**args)
        if result['error'] is not None:
            self.fail(f'page({args}) raised {result["error"]}')
        return result

    def test_snapshot_traversal_preserves_ties_filters_and_changed_limits(self) -> None:
        original = [
            event('z', 10, 'news'),
            event('a', 10, 'news'),
            event('é', 10, 'news'),
            event('中', 10, 'news'),
            event('late', 30, 'news'),
            event('ignored', 5, 'other'),
            event('middle', 20, 'news'),
        ]
        self.append(original)

        first = self.page(limit=2, tag='news')

        self.assertEqual(
            first['items'], [event('a', 10, 'news'), event('z', 10, 'news')]
        )
        self.assertIsNotNone(first['next_cursor'])
        self.append(
            [
                event('backdated', -1, 'news'),
                event('between', 15, 'news'),
                event('future', 100, 'news'),
            ]
        )
        seen = list(first['items'])
        cursor = first['next_cursor']
        limits = iter((1, 3, 2, 2, 2))
        while cursor is not None:
            current = self.page(limit=next(limits), tag='news', cursor=cursor)
            seen.extend(current['items'])
            cursor = current['next_cursor']

        expected = sorted(
            [entry for entry in original if entry['tag'] == 'news'],
            key=lambda entry: (entry['occurred_at'], entry['id']),
        )
        self.assertEqual(seen, expected)
        self.assertEqual(len({entry['id'] for entry in seen}), len(expected))

    def test_empty_and_full_final_pages_have_no_cursor(self) -> None:
        empty = self.page(limit=2)

        self.assertEqual(empty['items'], [])
        self.assertIsNone(empty['next_cursor'])

        self.append(
            [
                event('a', 1, ''),
                event('b', 2, 'x'),
                event('c', 3, ''),
                event('d', 4, ''),
            ]
        )
        for tag, expected in [
            (None, ['a', 'b', 'c', 'd']),
            ('', ['a', 'c', 'd']),
            ('missing', []),
        ]:
            with self.subTest(tag=tag):
                limit = len(expected) or 1

                final = self.page(limit=limit, tag=tag)

                self.assertEqual([entry['id'] for entry in final['items']], expected)
                self.assertIsNone(final['next_cursor'])

    def test_cursor_binds_filter_and_rejects_unsupported_input(self) -> None:
        self.append(
            [event('a', 1, 'news'), event('b', 2, 'news'), event('c', 3, 'other')]
        )
        first = self.page(limit=1, tag='news')
        self.assertIsNotNone(first['next_cursor'])

        for tag in (None, '', 'other'):
            with self.subTest(tag=tag):
                rejected = self.read(limit=1, tag=tag, cursor=first['next_cursor'])
                self.assertEqual(rejected['error'], 'CursorError')

        for invalid in ('', 'not-an-issued-cursor', '\x00'):
            with self.subTest(cursor=invalid):
                rejected = self.read(limit=1, tag='news', cursor=invalid)
                self.assertEqual(rejected['error'], 'CursorError')

        continuation = self.page(limit=100, tag='news', cursor=first['next_cursor'])

        self.assertEqual(continuation['items'], [event('b', 2, 'news')])
        self.assertIsNone(continuation['next_cursor'])

    def test_append_preserves_existing_ids_and_allocates_ingestion_order(self) -> None:
        self.append([event('first', 100, 'a'), event('second', -100, 'b')])

        appended = sandbox.observe(
            'feed',
            'append_after_duplicate',
            path=self.db,
            duplicate=event('first', 200, 'changed'),
            replacement=event('third', 0, 'c'),
        )

        if not appended['rejected']:
            self.fail('An existing event ID must be rejected.')
        self.assertGreater(appended['highwater'], appended['ceiling'])

        result = self.page(limit=100)

        self.assertEqual(
            result['items'],
            [
                event('second', -100, 'b'),
                event('third', 0, 'c'),
                event('first', 100, 'a'),
            ],
        )

    def test_limit_bounds_are_enforced(self) -> None:
        for limit in (-1, 0, 101):
            with self.subTest(limit=limit):
                rejected = self.read(limit=limit)
                self.assertIn(rejected['error'], {'CursorError', 'ValueError'})

    def test_cli_continuation_uses_persisted_snapshot(self) -> None:
        for identifier, occurred_at in [('a', 10), ('b', 20), ('c', 30)]:
            inserted = sandbox.observe(
                'feed',
                'cli',
                args=[
                    'append',
                    '--db',
                    self.db,
                    '--id',
                    identifier,
                    '--occurred-at',
                    str(occurred_at),
                    '--tag',
                    'news',
                ],
            )
            self.assertEqual(
                json.loads(inserted),
                {'id': identifier, 'occurred_at': occurred_at, 'tag': 'news'},
            )

        first = sandbox.observe(
            'feed',
            'cli',
            args=['page', '--db', self.db, '--limit', '1', '--tag', 'news'],
        )
        first_value = json.loads(first)
        self.assertEqual(
            first_value['items'], [{'id': 'a', 'occurred_at': 10, 'tag': 'news'}]
        )
        self.assertIsInstance(first_value['next_cursor'], str)
        self.append([event('backdated', 0, 'news'), event('later', 40, 'news')])

        rest = sandbox.observe(
            'feed',
            'cli',
            args=[
                'page',
                '--db',
                self.db,
                '--limit',
                '2',
                '--tag',
                'news',
                '--cursor',
                first_value['next_cursor'],
            ],
        )

        self.assertEqual(
            json.loads(rest),
            {
                'items': [
                    {'id': 'b', 'occurred_at': 20, 'tag': 'news'},
                    {'id': 'c', 'occurred_at': 30, 'tag': 'news'},
                ],
                'next_cursor': None,
            },
        )
