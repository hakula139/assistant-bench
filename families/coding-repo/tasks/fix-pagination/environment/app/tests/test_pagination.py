import unittest

from ledger.pagination import paginate


class PaginateTest(unittest.TestCase):
    def test_first_page(self):
        page = paginate(list(range(10)), 1, 3)
        self.assertEqual(page.items, [0, 1, 2])
        self.assertEqual(page.total_pages, 4)
        self.assertTrue(page.has_next)

    def test_last_partial_page(self):
        page = paginate(list(range(10)), 4, 3)
        self.assertEqual(page.items, [9])
        self.assertFalse(page.has_next)

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            paginate(list(range(10)), 5, 3)


if __name__ == "__main__":
    unittest.main()
