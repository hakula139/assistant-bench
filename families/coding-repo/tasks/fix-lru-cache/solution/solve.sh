#!/usr/bin/env bash

set -euo pipefail

cd /app
python3 - <<'PY'
import pathlib

path = pathlib.Path("cachelite/lru.py")
text = path.read_text()
text = text.replace(
    '''        value = self._data.get(key)
        if not value:
            return default
        self._data.move_to_end(key)
        return value
''',
    '''        if key not in self._data:
            return default
        self._data.move_to_end(key)
        return self._data[key]
''')
text = text.replace(
    '''        if len(self._data) >= self.capacity:
            old_key, old_value = self._data.popitem(last=False)
            if self.on_evict is not None:
                self.on_evict(old_key, old_value)
        self._data[key] = value
''',
    '''        if key in self._data:
            self._data.move_to_end(key)
        elif len(self._data) >= self.capacity:
            old_key, old_value = self._data.popitem(last=False)
            if self.on_evict is not None:
                self.on_evict(old_key, old_value)
        self._data[key] = value
''')
path.write_text(text)

tests = pathlib.Path("tests/test_lru.py")
tests.write_text(tests.read_text().replace('''

if __name__ == "__main__":''', '''
    def test_falsy_value_is_cached(self):
        cache = LRUCache(2)
        cache.put("a", 0)
        self.assertEqual(cache.get("a", "missing"), 0)

    def test_replace_when_full_keeps_other_entries(self):
        evicted = []
        cache = LRUCache(2, on_evict=lambda k, v: evicted.append(k))
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 3)
        self.assertEqual(cache.keys(), ["b", "a"])
        self.assertEqual(evicted, [])


if __name__ == "__main__":'''))
PY
