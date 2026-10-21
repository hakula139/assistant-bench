import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from assistant_bench.arena.store import Ballot, Choice, Side, VoteStore, votes_file
from assistant_bench.scoring.ranking import Comparison


AT = datetime(2026, 10, 21, 9, tzinfo=UTC)
LARGE = Side(system='large', fingerprint='fp-large', job='job-1', trial='c1__large')
SMALL = Side(system='small', fingerprint=None, job='job-1', trial='c1__small')


def ballot(session: str, **fields: object) -> Ballot:
    return Ballot.model_validate(
        {'session': session, 'case': 'c1', 'a': LARGE, 'b': SMALL, 'choice': 'a'}
        | fields
    )


class TestVoteStore:
    def test_keeps_votes_across_a_reopen(
        self, store: VoteStore, data_dir: Path
    ) -> None:
        session = store.start_session('f', AT)
        recorded = store.record(
            ballot(
                session.id,
                flags={'punctuation', 'length'},
                note='Drops a fact.',
                duration_sec=12.5,
            ),
            AT + timedelta(seconds=30),
        )
        store.close()

        reopened = VoteStore(data_dir / 'votes.sqlite')

        assert reopened.votes('f') == [recorded]
        assert reopened.session(session.id) == session
        reopened.close()
        assert recorded.flags == {'punctuation', 'length'}
        assert recorded.recorded_at == AT + timedelta(seconds=30)

    def test_reads_only_the_family_s_votes(self, store: VoteStore) -> None:
        mine = store.start_session('f', AT)
        other = store.start_session('g', AT)
        kept = store.record(ballot(mine.id), AT)
        store.record(ballot(other.id), AT)

        assert store.votes('f') == [kept]

    def test_withdraws_an_undone_vote(self, store: VoteStore) -> None:
        session = store.start_session('f', AT)
        first = store.record(ballot(session.id), AT)
        second = store.record(ballot(session.id, choice='tie'), AT)

        store.undo(second.id, AT)

        assert store.votes('f') == [first]

    @pytest.mark.parametrize(
        'statement', ["UPDATE votes SET choice = 'b'", 'DELETE FROM votes']
    )
    def test_refuses_to_change_a_recorded_vote(
        self, store: VoteStore, statement: str
    ) -> None:
        session = store.start_session('f', AT)
        store.record(ballot(session.id), AT)
        db = sqlite3.connect(votes_file())

        with closing(db), pytest.raises(sqlite3.IntegrityError, match='append-only'):
            db.execute(statement)

    def test_orders_sessions_by_start(self, store: VoteStore) -> None:
        later = store.start_session('f', AT + timedelta(days=1))
        earlier = store.start_session('f', AT)

        assert store.sessions('f') == [earlier, later]
        assert store.session('missing') is None


class TestVote:
    class TestComparison:
        @pytest.mark.parametrize(
            ('choice', 'score'),
            [('a', 1.0), ('b', 0.0), ('tie', 0.5), ('both_bad', 0.5)],
        )
        def test_scores_the_left_side(
            self, store: VoteStore, choice: Choice, score: float
        ) -> None:
            session = store.start_session('f', AT)

            vote = store.record(ballot(session.id, choice=choice), AT)

            assert vote.comparison() == Comparison('large', 'small', score, 'c1')
