import unittest

from ledger import paginate, total_pages


class TotalPagesTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(total_pages(0, 5), 1)
        self.assertEqual(total_pages(1, 1), 1)
        self.assertEqual(total_pages(9, 3), 3)
        self.assertEqual(total_pages(10, 3), 4)
        self.assertEqual(total_pages(11, 10), 2)

    def test_invalid_per_page(self):
        with self.assertRaises(ValueError):
            total_pages(10, 0)


class PaginateEdgesTest(unittest.TestCase):
    def test_middle_page(self):
        page = paginate(tuple(range(10)), 2, 3)
        self.assertEqual(page.items, [3, 4, 5])
        self.assertEqual(page.number, 2)
        self.assertTrue(page.has_next)

    def test_exact_multiple(self):
        page = paginate(list(range(9)), 3, 3)
        self.assertEqual(page.items, [6, 7, 8])
        self.assertEqual(page.total_pages, 3)
        self.assertFalse(page.has_next)

    def test_empty_listing(self):
        page = paginate([], 1, 4)
        self.assertEqual(page.items, [])
        self.assertEqual(page.total_pages, 1)
        self.assertFalse(page.has_next)

    def test_page_zero(self):
        with self.assertRaises(ValueError):
            paginate([1, 2, 3], 0, 2)

    def test_past_end(self):
        with self.assertRaises(ValueError):
            paginate([1, 2, 3], 3, 2)
