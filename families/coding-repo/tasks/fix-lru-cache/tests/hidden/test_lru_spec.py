import unittest

from cachelite import LRUCache


class LRUSpecTest(unittest.TestCase):
    def test_falsy_values(self):
        cache = LRUCache(4)
        for key, value in (("zero", 0), ("empty", ""), ("list", []), ("none", None)):
            cache.put(key, value)
        self.assertEqual(cache.get("zero", "default"), 0)
        self.assertEqual(cache.get("empty", "default"), "")
        self.assertEqual(cache.get("list", "default"), [])
        self.assertIsNone(cache.get("none", "default"))
        self.assertEqual(cache.get("absent", "default"), "default")

    def test_get_of_falsy_value_refreshes(self):
        cache = LRUCache(2)
        cache.put("a", 0)
        cache.put("b", 1)
        cache.get("a")
        cache.put("c", 2)
        self.assertEqual(cache.keys(), ["a", "c"])

    def test_replace_when_full(self):
        evicted = []
        cache = LRUCache(2, on_evict=lambda k, v: evicted.append(k))
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.get("a"), 10)
        self.assertEqual(cache.get("b"), 2)
        self.assertEqual(evicted, [])

    def test_replace_refreshes_recency(self):
        evicted = []
        cache = LRUCache(2, on_evict=lambda k, v: evicted.append(k))
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 3)
        cache.put("c", 4)
        self.assertEqual(cache.keys(), ["a", "c"])
        self.assertEqual(evicted, ["b"])

    def test_capacity_one(self):
        evicted = []
        cache = LRUCache(1, on_evict=lambda k, v: evicted.append((k, v)))
        cache.put("a", 1)
        cache.put("a", 2)
        cache.put("b", 3)
        self.assertEqual(cache.keys(), ["b"])
        self.assertEqual(evicted, [("a", 2)])

    def test_pop_not_reported(self):
        evicted = []
        cache = LRUCache(2, on_evict=lambda k, v: evicted.append(k))
        cache.put("a", 1)
        cache.pop("a")
        cache.put("b", 2)
        cache.put("c", 3)
        self.assertEqual(evicted, [])
