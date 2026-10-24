import unittest

from cachelite import LRUCache


class LRUCacheTest(unittest.TestCase):
    def test_get_and_put(self):
        cache = LRUCache(2)
        cache.put("a", 1)
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("b"))

    def test_evicts_least_recently_used(self):
        evicted = []
        cache = LRUCache(2, on_evict=lambda k, v: evicted.append((k, v)))
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")
        cache.put("c", 3)
        self.assertEqual(cache.keys(), ["a", "c"])
        self.assertEqual(evicted, [("b", 2)])

    def test_pop(self):
        cache = LRUCache(2)
        cache.put("a", 1)
        self.assertEqual(cache.pop("a"), 1)
        self.assertEqual(cache.pop("a", None), None)
        with self.assertRaises(KeyError):
            cache.pop("a")

    def test_invalid_capacity(self):
        with self.assertRaises(ValueError):
            LRUCache(0)


if __name__ == "__main__":
    unittest.main()
