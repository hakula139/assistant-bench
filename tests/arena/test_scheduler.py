from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import permutations

import numpy as np
import pytest
from scipy import special

from assistant_bench.arena import scheduler
from assistant_bench.arena.resolution import MatchedVote, reconcile
from assistant_bench.arena.scheduler import Board, Draw, Pool, draw, pair_weights
from assistant_bench.arena.store import Choice, Side, Vote
from assistant_bench.families import Gate
from assistant_bench.outputs import Output
from assistant_bench.scoring.ranking import ELO_PER_LOGIT, Comparison, Ratings
from support import make_output


NOW = datetime(2026, 10, 21, 9, tzinfo=UTC)
SYSTEMS = ('large', 'medium', 'small')


def make_pool(
    systems: tuple[str, ...] = SYSTEMS, cases: int = 10, **fields: object
) -> Pool:
    outputs = [
        make_output(
            trial=f'c{case}__{system}',
            case=f'c{case}',
            system=system,
            text=f'{system} on c{case}',
            gate_failures=[],
            **fields,
        )
        for case in range(cases)
        for system in systems
    ]
    return Pool.build(systems, {f'c{case}' for case in range(cases)}, outputs)


def matched(pool: Pool, votes: list[Vote]) -> list[MatchedVote]:
    return reconcile(votes, pool.outputs)


def vote_on(
    picked: Draw, choice: Choice, vote_id: int, at: datetime = NOW, **fields: object
) -> Vote:
    return Vote.model_validate(
        {
            'id': vote_id,
            'recorded_at': at,
            'session': 's',
            'case': picked.case,
            'a': Side.of(picked.a),
            'b': Side.of(picked.b),
            'choice': choice,
            'duration_sec': 5.0,
        }
        | fields
    )


def votes_between(
    pool: Pool, left: str, right: str, choices: list[Choice], start: int = 1
) -> list[Vote]:
    return [
        vote_on(
            Draw(
                case=f'c{i % 10}',
                a=pool.outputs_by_case[f'c{i % 10}'][left][0],
                b=pool.outputs_by_case[f'c{i % 10}'][right][0],
            ),
            choice,
            start + i,
        )
        for i, choice in enumerate(choices)
    ]


class TestPool:
    class TestBuild:
        def test_keeps_the_passing_outputs_of_listed_systems_on_current_cases(
            self,
        ) -> None:
            outputs: list[Output] = [
                make_output(trial='1', system='large', text='Hi.', gate_failures=[]),
                make_output(
                    trial='2', system='large', text='', gate_failures=[Gate.EMPTY]
                ),
                make_output(trial='3', system='large', error='TimeoutError'),
                make_output(trial='4', system='retired', gate_failures=[]),
                make_output(trial='5', case='old', system='large', gate_failures=[]),
            ]

            pool = Pool.build(['large', 'small'], {'c'}, outputs)

            (by_system,) = pool.outputs_by_case.values()
            assert list(by_system) == ['large']
            assert [output.trial for output in by_system['large']] == ['1']
            assert [output.trial for output in pool.outputs] == ['1', '2', '3']

    class TestPairings:
        def test_leaves_out_samples_with_identical_text(self) -> None:
            outputs = [
                make_output(
                    trial=f'{system}{i}', system=system, text=text, gate_failures=[]
                )
                for system in ('large', 'small')
                for i, text in enumerate(('Same.', f'{system} differs.'))
            ]
            pool = Pool.build(['large', 'small'], {'c'}, outputs)

            pairings = pool.pairings('c', 'large', 'small')

            assert sorted((a.trial, b.trial) for a, b in pairings) == [
                ('large0', 'small1'),
                ('large1', 'small0'),
                ('large1', 'small1'),
            ]


class TestBoard:
    class TestReset:
        def test_refits_on_the_next_draw(self, fits: list[int]) -> None:
            pool = make_pool(systems=('large', 'small'))
            votes = matched(pool, votes_between(pool, 'large', 'small', ['a'] * 4))
            board = Board()
            board.ratings(votes, [], {'large', 'small'})

            board.reset()
            board.ratings(votes, [], {'large', 'small'})

            assert fits == [4, 4]

    class TestRatings:
        def test_refits_once_refit_votes_are_cast_or_undone(
            self, fits: list[int]
        ) -> None:
            pool = make_pool(systems=('large', 'small'))
            votes = matched(pool, votes_between(pool, 'large', 'small', ['a'] * 9))
            board = Board()

            for count in (4, 8, 9, 5, 4):
                board.ratings(votes[:count], [], {'large', 'small'})

            assert fits == [4, 9, 4]

        def test_refits_once_refit_votes_are_undone_and_recast(
            self, fits: list[int]
        ) -> None:
            pool = make_pool(systems=('large', 'small'))
            votes = matched(pool, votes_between(pool, 'large', 'small', ['a'] * 7))
            board = Board()
            board.ratings(votes[:4], [], {'large', 'small'})

            board.ratings(votes[:2] + votes[4:6], [], {'large', 'small'})
            board.ratings(votes[:1] + votes[4:7], [], {'large', 'small'})

            assert fits == [4, 4]

        def test_fits_the_settled_results_with_the_votes(self, fits: list[int]) -> None:
            pool = make_pool(systems=('large', 'small'))
            votes = matched(pool, votes_between(pool, 'large', 'small', ['a'] * 4))
            settled = [Comparison('large', 'small', 1.0, 'c9', 0.25, sided=False)]

            Board().ratings(votes, settled, {'large', 'small'})

            assert fits == [5]

        def test_refits_for_a_system_it_lacks(self, fits: list[int]) -> None:
            pool = make_pool()
            votes = matched(
                pool,
                votes_between(pool, 'large', 'small', ['a'] * 4)
                + votes_between(pool, 'medium', 'small', ['a'], start=5),
            )
            board = Board()

            board.ratings(votes[:4], [], {'large', 'small'})
            ratings = board.ratings(votes, [], set(SYSTEMS))

            assert fits == [4, 5]
            assert ratings.systems == SYSTEMS


class TestDraw:
    def test_seeds_every_ordered_pair_before_adapting(self) -> None:
        pool = make_pool()
        rng = np.random.default_rng(0)
        votes: list[Vote] = []

        for vote_id in range(1, 13):
            picked = draw(pool, matched(pool, votes), set(), NOW, rng, Board())
            assert picked is not None
            votes.append(vote_on(picked, 'a', vote_id))

        counts = Counter((vote.a.system, vote.b.system) for vote in votes)
        assert counts == dict.fromkeys(permutations(SYSTEMS, 2), 2)

    def test_draws_only_cases_the_session_has_not_used(self) -> None:
        pool = make_pool(cases=3)
        rng = np.random.default_rng(0)

        cases = set()
        for _ in range(10):
            picked = draw(pool, [], {'c0', 'c2'}, NOW, rng, Board())
            assert picked is not None
            cases.add(picked.case)

        assert cases == {'c1'}
        assert draw(pool, [], {'c0', 'c1', 'c2'}, NOW, rng, Board()) is None

    def test_favors_the_close_pair_once_seeded(self) -> None:
        pool = make_pool()
        votes = (
            votes_between(pool, 'large', 'medium', ['a'] * 10)
            + votes_between(pool, 'medium', 'large', ['b'] * 10, start=11)
            + votes_between(pool, 'large', 'small', ['a'] * 10, start=21)
            + votes_between(pool, 'small', 'large', ['b'] * 10, start=31)
            + votes_between(pool, 'medium', 'small', ['a', 'b'] * 2, start=41)
            + votes_between(pool, 'small', 'medium', ['a', 'b'] * 2, start=45)
        )
        rng = np.random.default_rng(0)
        board = Board()

        pairs = set()
        for _ in range(10):
            picked = draw(pool, matched(pool, votes), set(), NOW, rng, board)
            assert picked is not None
            pairs.add(frozenset((picked.a.system, picked.b.system)))

        assert pairs == {frozenset(('medium', 'small'))}

    def test_puts_the_less_shown_order_on_the_left(self) -> None:
        pool = make_pool(systems=('large', 'small'))
        votes = votes_between(pool, 'large', 'small', ['a', 'b', 'tie']) + (
            votes_between(pool, 'small', 'large', ['a', 'b'], start=4)
        )
        rng = np.random.default_rng(0)

        picked = draw(pool, matched(pool, votes), set(), NOW, rng, Board())

        assert picked is not None
        assert (picked.a.system, picked.b.system) == ('small', 'large')

    @pytest.mark.parametrize(
        ('age', 'repeated', 'expected'),
        [
            (timedelta(days=1), False, True),
            (timedelta(hours=23), False, False),
            (timedelta(days=2), True, False),
        ],
    )
    def test_repeats_a_vote_a_day_old_with_sides_swapped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        age: timedelta,
        repeated: bool,
        expected: bool,
    ) -> None:
        monkeypatch.setattr(scheduler, 'REPEAT_SHARE', 1.0)
        pool = make_pool(systems=('large', 'small'))
        (original,) = votes_between(pool, 'large', 'small', ['a'])
        original = original.model_copy(update={'recorded_at': NOW - age})
        votes = [original]
        if repeated:
            votes.append(
                original.model_copy(
                    update={'id': 2, 'a': original.b, 'b': original.a, 'repeat_of': 1}
                )
            )

        picked = draw(
            pool, matched(pool, votes), set(), NOW, np.random.default_rng(0), Board()
        )

        assert picked is not None
        assert (picked.repeat_of == 1) is expected
        if expected:
            assert (picked.case, Side.of(picked.a), Side.of(picked.b)) == (
                original.case,
                original.b,
                original.a,
            )


class TestPairWeights:
    @staticmethod
    def ratings(score: list[float], lower: list[float], upper: list[float]) -> Ratings:
        return Ratings(
            systems=('a', 'b', 'c', 'd'),
            score=np.array(score),
            lower=np.array(lower),
            upper=np.array(upper),
            draws=np.empty((0, 4)),
            position=0.0,
        )

    def test_favors_close_pairs_with_few_votes_among_those_that_overlap(
        self,
    ) -> None:
        ratings = self.ratings(
            score=[1000, 1000, 1100, 1300],
            lower=[950, 950, 1000, 1250],
            upper=[1050, 1050, 1200, 1350],
        )
        pairs = [('a', 'b'), ('a', 'c'), ('b', 'c'), ('c', 'd')]

        weights = pair_weights(ratings, pairs, Counter({('c', 'b'): 3}))

        p = special.expit(100 / ELO_PER_LOGIT)
        assert weights == pytest.approx(
            {('a', 'b'): 0.25, ('a', 'c'): p * (1 - p), ('b', 'c'): p * (1 - p) / 4}
        )

    def test_weighs_every_pair_once_all_separate(self) -> None:
        ratings = self.ratings(
            score=[1000, 1200, 1400, 1600],
            lower=[990, 1190, 1390, 1590],
            upper=[1010, 1210, 1410, 1610],
        )

        weights = pair_weights(ratings, [('a', 'b'), ('a', 'c')], Counter())

        p, q = (special.expit(gap / ELO_PER_LOGIT) for gap in (200, 400))
        assert weights == pytest.approx(
            {('a', 'b'): p * (1 - p), ('a', 'c'): q * (1 - q)}
        )
