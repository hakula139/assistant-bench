import sqlite3
from contextlib import nullcontext

import pytest

from support import SQLiteDatabase, arena_database


class TestArenaDatabase:
    @pytest.mark.parametrize('fails', [False, True], ids=['normal', 'exceptional'])
    def test_closes_the_store_after_the_context_exits(self, fails: bool) -> None:
        store: SQLiteDatabase | None = None
        outcome = (
            pytest.raises(ValueError, match='Interrupted') if fails else nullcontext()
        )

        with outcome, arena_database() as database:
            store = database
            assert database.execute('SELECT 1 AS value').rows == [{'value': 1}]
            if fails:
                raise ValueError('Interrupted')

        assert store is not None
        with pytest.raises(sqlite3.ProgrammingError, match='closed database'):
            store.execute('SELECT 1')
