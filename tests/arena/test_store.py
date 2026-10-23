import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from assistant_bench.arena.store import Choice, Defects, VoteStore, votes_file
from assistant_bench.scoring.ranking import Comparison
from support import AT, make_ballot, make_output, make_vote


LARGE = make_output(
    trial='c1__large', job='job-1', case='c1', system='large', fingerprint='fp-large'
)
SMALL = make_output(trial='c1__small', job='job-1', case='c1', system='small')


class TestVote:
    class TestComparison:
        @pytest.mark.parametrize(
            ('choice', 'score'),
            [('a', 1.0), ('b', 0.0), ('tie', 0.5), ('both_bad', 0.5)],
        )
        def test_scores_the_left_side(self, choice: Choice, score: float) -> None:
            vote = make_vote(LARGE, SMALL, choice=choice)

            comparison = vote.comparison()

            assert comparison == Comparison('large', 'small', score, 'c1')


class TestVoteStore:
    class TestInit:
        def test_keeps_votes_across_a_reopen(
            self, store: VoteStore, data_dir: Path
        ) -> None:
            session = store.start_session('f', AT)
            ballot = make_ballot(
                LARGE,
                SMALL,
                session=session.id,
                defects=Defects(
                    a=frozenset({'tone', 'length'}), b=frozenset({'facts'})
                ),
                note='Drops a fact.',
                duration_sec=12.5,
            )
            recorded = store.record(ballot, AT + timedelta(seconds=30))
            store.close()

            with closing(VoteStore(data_dir / 'votes.sqlite')) as reopened:
                votes = reopened.votes('f')
                restored = reopened.session(session.id)

            assert votes == [recorded]
            assert restored == session

        @pytest.mark.parametrize(
            ('version', 'step'),
            [
                (
                    1,
                    "Close its families' rounds with the bench revision that wrote "
                    "it, which `git log -S 'SCHEMA_VERSION = 1' -- "
                    'src/assistant_bench/arena/store.py` finds, then move the file '
                    'aside so `bench serve` starts a new store.',
                ),
                (3, 'A newer bench wrote it, so update this checkout.'),
            ],
            ids=['older', 'newer'],
        )
        def test_refuses_a_store_of_another_schema_version(
            self, data_dir: Path, version: int, step: str
        ) -> None:
            path = data_dir / 'votes.sqlite'
            path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(path)) as db:
                db.execute(f'PRAGMA user_version = {version}')

            with pytest.raises(ValueError) as raised:
                VoteStore(path)

            assert str(raised.value) == (
                f'The vote store `{path}` has schema version {version}, while this '
                f'bench reads version 2. {step}'
            )

    class TestSession:
        def test_finds_no_session_it_never_started(self, store: VoteStore) -> None:
            store.start_session('f', AT)

            session = store.session('missing')

            assert session is None

    class TestSessions:
        def test_orders_sessions_by_start(self, store: VoteStore) -> None:
            later = store.start_session('f', AT + timedelta(days=1))
            earlier = store.start_session('f', AT)

            sessions = store.sessions('f')

            assert sessions == [earlier, later]

    class TestRecord:
        def test_returns_the_vote_with_its_id_and_time(self, store: VoteStore) -> None:
            session = store.start_session('f', AT)
            store.record(make_ballot(LARGE, SMALL, session=session.id), AT)

            vote = store.record(
                make_ballot(LARGE, SMALL, session=session.id), AT + timedelta(hours=1)
            )

            assert (vote.id, vote.recorded_at) == (2, AT + timedelta(hours=1))

        @pytest.mark.parametrize(
            'statement',
            [
                "UPDATE votes SET choice = 'b'",
                'DELETE FROM votes',
                'INSERT OR REPLACE INTO votes SELECT id, session, case_id, a_system, '
                'a_fingerprint, a_job, a_trial, b_system, b_fingerprint, b_job, '
                "b_trial, 'b', a_defects, b_defects, note, duration_sec, repeat_of, "
                'recorded_at FROM votes',
            ],
            ids=['update', 'delete', 'replace'],
        )
        def test_refuses_to_change_a_recorded_vote(
            self, store: VoteStore, statement: str
        ) -> None:
            session = store.start_session('f', AT)
            store.record(make_ballot(LARGE, SMALL, session=session.id), AT)
            db = sqlite3.connect(votes_file())

            with (
                closing(db),
                pytest.raises(sqlite3.IntegrityError, match='append-only'),
            ):
                db.execute(statement)

            assert [vote.choice for vote in store.votes('f')] == ['a']

    class TestUndo:
        def test_withdraws_an_undone_vote(self, store: VoteStore) -> None:
            session = store.start_session('f', AT)
            first = store.record(make_ballot(LARGE, SMALL, session=session.id), AT)
            second = store.record(
                make_ballot(LARGE, SMALL, session=session.id, choice='tie'), AT
            )

            store.undo(second.id, AT)

            assert store.votes('f') == [first]

        def test_refuses_to_undo_a_vote_it_never_recorded(
            self, store: VoteStore
        ) -> None:
            with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
                store.undo(999, AT)

    class TestVotes:
        def test_reads_only_the_family_s_votes(self, store: VoteStore) -> None:
            mine = store.start_session('f', AT)
            other = store.start_session('g', AT)
            kept = store.record(make_ballot(LARGE, SMALL, session=mine.id), AT)
            store.record(make_ballot(LARGE, SMALL, session=other.id), AT)

            votes = store.votes('f')

            assert votes == [kept]
