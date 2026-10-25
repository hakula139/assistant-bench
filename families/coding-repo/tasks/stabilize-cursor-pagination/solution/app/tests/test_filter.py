import tempfile
import unittest
from pathlib import Path

from eventfeed import Event, Store, page


class FilterTest(unittest.TestCase):
    def test_empty_tag_differs_from_an_unfiltered_feed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'feed.db'
            with Store(db) as store:
                store.append(Event('a', 1, ''))
                store.append(Event('b', 2, 'other'))

            filtered = page(db, limit=1, tag='')

            self.assertEqual(filtered.items, [Event('a', 1, '')])
            self.assertIsNone(filtered.next_cursor)
