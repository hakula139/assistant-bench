import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eventfeed import CursorError, Event, Store, page


class PageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / 'feed.db'

    def append(self, events: list[Event]) -> None:
        with Store(self.db) as store:
            for event in events:
                store.append(event)

    def test_snapshot_traversal_preserves_ties_filters_and_changed_limits(self) -> None:
        original = [
            Event('z', 10, 'news'),
            Event('a', 10, 'news'),
            Event('é', 10, 'news'),
            Event('中', 10, 'news'),
            Event('late', 30, 'news'),
            Event('ignored', 5, 'other'),
            Event('middle', 20, 'news'),
        ]
        self.append(original)

        first = page(self.db, limit=2, tag='news')

        self.assertEqual(first.items, [Event('a', 10, 'news'), Event('z', 10, 'news')])
        self.assertIsNotNone(first.next_cursor)
        self.append(
            [
                Event('backdated', -1, 'news'),
                Event('between', 15, 'news'),
                Event('future', 100, 'news'),
            ]
        )
        seen = list(first.items)
        cursor = first.next_cursor
        limits = iter((1, 3, 2, 2, 2))
        while cursor is not None:
            current = page(self.db, limit=next(limits), tag='news', cursor=cursor)
            seen.extend(current.items)
            cursor = current.next_cursor

        expected = sorted(
            [event for event in original if event.tag == 'news'],
            key=lambda event: (event.occurred_at, event.id),
        )
        self.assertEqual(seen, expected)
        self.assertEqual(len({event.id for event in seen}), len(expected))

    def test_empty_and_full_final_pages_have_no_cursor(self) -> None:
        empty = page(self.db, limit=2)

        self.assertEqual(empty.items, [])
        self.assertIsNone(empty.next_cursor)

        self.append(
            [
                Event('a', 1, ''),
                Event('b', 2, 'x'),
                Event('c', 3, ''),
                Event('d', 4, ''),
            ]
        )
        for tag, expected in [
            (None, ['a', 'b', 'c', 'd']),
            ('', ['a', 'c', 'd']),
            ('missing', []),
        ]:
            with self.subTest(tag=tag):
                limit = len(expected) or 1

                final = page(self.db, limit=limit, tag=tag)

                self.assertEqual([event.id for event in final.items], expected)
                self.assertIsNone(final.next_cursor)

    def test_cursor_binds_filter_and_rejects_unsupported_input(self) -> None:
        self.append(
            [Event('a', 1, 'news'), Event('b', 2, 'news'), Event('c', 3, 'other')]
        )
        first = page(self.db, limit=1, tag='news')
        self.assertIsNotNone(first.next_cursor)

        for tag in (None, '', 'other'):
            with self.subTest(tag=tag), self.assertRaises(CursorError):
                page(self.db, limit=1, tag=tag, cursor=first.next_cursor)

        for invalid in ('', 'not-an-issued-cursor', '\x00'):
            with self.subTest(cursor=invalid), self.assertRaises(CursorError):
                page(self.db, limit=1, tag='news', cursor=invalid)

        continuation = page(self.db, limit=100, tag='news', cursor=first.next_cursor)

        self.assertEqual(continuation.items, [Event('b', 2, 'news')])
        self.assertIsNone(continuation.next_cursor)

    def test_append_preserves_existing_ids_and_allocates_ingestion_order(self) -> None:
        self.append([Event('first', 100, 'a'), Event('second', -100, 'b')])
        with Store(self.db) as store:
            ceiling = store.highwater()

            try:
                store.append(Event('first', 200, 'changed'))
            except Exception:
                store.append(Event('third', 0, 'c'))
            else:
                self.fail('An existing event ID must be rejected.')

            self.assertGreater(store.highwater(), ceiling)

        result = page(self.db, limit=100)

        self.assertEqual(
            result.items,
            [
                Event('second', -100, 'b'),
                Event('third', 0, 'c'),
                Event('first', 100, 'a'),
            ],
        )

    def test_limit_bounds_are_enforced(self) -> None:
        for limit in (-1, 0, 101):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                page(self.db, limit=limit)

    def test_cli_continuation_uses_persisted_snapshot(self) -> None:
        command = [sys.executable, '-m', 'eventfeed']
        for identifier, occurred_at in [('a', 10), ('b', 20), ('c', 30)]:
            inserted = subprocess.run(
                [
                    *command,
                    'append',
                    '--db',
                    str(self.db),
                    '--id',
                    identifier,
                    '--occurred-at',
                    str(occurred_at),
                    '--tag',
                    'news',
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            self.assertEqual(
                json.loads(inserted.stdout),
                {'id': identifier, 'occurred_at': occurred_at, 'tag': 'news'},
            )

        first = subprocess.run(
            [*command, 'page', '--db', str(self.db), '--limit', '1', '--tag', 'news'],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        first_value = json.loads(first.stdout)
        self.assertEqual(
            first_value['items'], [{'id': 'a', 'occurred_at': 10, 'tag': 'news'}]
        )
        self.assertIsInstance(first_value['next_cursor'], str)
        self.append([Event('backdated', 0, 'news'), Event('later', 40, 'news')])

        rest = subprocess.run(
            [
                *command,
                'page',
                '--db',
                str(self.db),
                '--limit',
                '2',
                '--tag',
                'news',
                '--cursor',
                first_value['next_cursor'],
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        self.assertEqual(
            json.loads(rest.stdout),
            {
                'items': [
                    {'id': 'b', 'occurred_at': 20, 'tag': 'news'},
                    {'id': 'c', 'occurred_at': 30, 'tag': 'news'},
                ],
                'next_cursor': None,
            },
        )
