import tempfile
import unittest
from pathlib import Path

from eventfeed import Event, Store, page


class PageTest(unittest.TestCase):
    def test_backdated_insert_does_not_shift_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'feed.db'
            with Store(db) as store:
                for event in [
                    Event('a', 10, ''),
                    Event('b', 20, ''),
                    Event('c', 30, ''),
                ]:
                    store.append(event)

            first = page(db, limit=1)
            self.assertIsNotNone(first.next_cursor)
            with Store(db) as store:
                store.append(Event('new', 0, ''))

            rest = page(db, limit=2, cursor=first.next_cursor)

            self.assertEqual(first.items, [Event('a', 10, '')])
            self.assertEqual(rest.items, [Event('b', 20, ''), Event('c', 30, '')])
            self.assertIsNone(rest.next_cursor)
