from collections import Counter
from collections.abc import Callable, Iterable
from datetime import timedelta
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
from assistant_bench.scoring.ranking import ANCHOR, ELO_PER_LOGIT, Comparison, Ratings
from support import AT, make_output, make_vote, passing_outputs


SYSTEMS = ('large', 'medium', 'small')


def make_pool(
    systems: tuple[str, ...] = SYSTEMS, cases: int = 10, **fields: object
) -> Pool:
    names = [f'c{case}' for case in range(cases)]
    outputs = passing_outputs(names, systems, text='{system} on {case}', **fields)
    return Pool.build(systems, set(names), outputs.values())


def matched(pool: Pool, votes: list[Vote]) -> list[MatchedVote]:
    return reconcile(votes, pool.outputs)


def vote_on(picked: Draw, choice: Choice, vote_id: int) -> Vote:
    return make_vote(picked.a, picked.b, id=vote_id, choice=choice)


def votes_between(
    pool: Pool, left: str, right: str, choices: list[Choice], start: int = 1
) -> list[Vote]:
    return [
        make_vote(
            pool.outputs_by_case[f'c{i % 10}'][left][0],
            pool.outputs_by_case[f'c{i % 10}'][right][0],
            id=start + i,
            choice=choice,
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
            board = Board(pool.systems)
            board.ratings(votes, [], {'large', 'small'})

            board.reset()
            board.ratings(votes, [], {'large', 'small'})

            assert fits == [4, 4]

    class TestRatings:
        @pytest.mark.parametrize(
            ('subsets', 'expected'),
            [
                ([range(4), range(8), range(9), range(5), range(4)], [4, 9, 4]),
                ([range(4), [0, 1, 4, 5], [0, 4, 5, 6]], [4, 4]),
            ],
            ids=['cast-then-undone', 'undone-and-recast'],
        )
        def test_refits_once_refit_votes_changed_since_the_last_fit(
            self, fits: list[int], subsets: list[Iterable[int]], expected: list[int]
        ) -> None:
            pool = make_pool(systems=('large', 'small'))
            votes = matched(pool, votes_between(pool, 'large', 'small', ['a'] * 9))
            board = Board(pool.systems)

            for subset in subsets:
                board.ratings([votes[i] for i in subset], [], {'large', 'small'})

            assert fits == expected

        def test_fits_the_settled_results_with_the_votes(self, fits: list[int]) -> None:
            pool = make_pool(systems=('large', 'small'))
            votes = matched(pool, votes_between(pool, 'large', 'small', ['a'] * 4))
            settled = [Comparison('large', 'small', 1.0, 'c9', 0.25, sided=False)]

            Board(pool.systems).ratings(votes, settled, {'large', 'small'})

            assert fits == [5]

        def test_refits_for_a_system_it_lacks(self, fits: list[int]) -> None:
            pool = make_pool()
            votes = matched(
                pool,
                votes_between(pool, 'large', 'small', ['a'] * 4)
                + votes_between(pool, 'medium', 'small', ['a'], start=5),
            )
            board = Board(pool.systems)

            board.ratings(votes[:4], [], {'large', 'small'})
            ratings = board.ratings(votes, [], set(SYSTEMS))

            assert fits == [4, 5]
            assert ratings.systems == SYSTEMS

        def test_roots_the_fit_at_the_most_voted_system(self) -> None:
            pool = make_pool()
            votes = matched(
                pool,
                votes_between(pool, 'medium', 'large', ['a'] * 3)
                + votes_between(pool, 'medium', 'small', ['a'] * 3, start=4)
                + votes_between(pool, 'large', 'small', ['a'], start=7),
            )

            ratings = Board(pool.systems).ratings(votes, [], set(SYSTEMS))

            assert ratings.score[ratings.systems.index('medium')] == ANCHOR


class TestDraw:
    def test_seeds_every_ordered_pair_fewest_votes_first(self) -> None:
        pool = make_pool()
        rng = np.random.default_rng(0)
        votes: list[Vote] = []
        counts = []

        for vote_id in range(1, 13):
            picked = draw(pool, matched(pool, votes), set(), AT, rng, Board(SYSTEMS))
            assert picked is not None
            votes.append(vote_on(picked, 'a', vote_id))
            if vote_id % 6 == 0:
                counts.append(Counter((v.a.system, v.b.system) for v in votes))

        assert counts == [
            dict.fromkeys(permutations(SYSTEMS, 2), 1),
            dict.fromkeys(permutations(SYSTEMS, 2), 2),
        ]

    def test_draws_only_cases_the_session_has_not_used(self) -> None:
        pool = make_pool(cases=3)
        rng = np.random.default_rng(0)

        cases = set()
        for _ in range(10):
            picked = draw(pool, [], {'c0', 'c2'}, AT, rng, Board(SYSTEMS))
            assert picked is not None
            cases.add(picked.case)

        assert cases == {'c1'}
        assert draw(pool, [], {'c0', 'c1', 'c2'}, AT, rng, Board(SYSTEMS)) is None

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
        board = Board(SYSTEMS)

        pairs = set()
        for _ in range(10):
            picked = draw(pool, matched(pool, votes), set(), AT, rng, board)
            assert picked is not None
            pairs.add(frozenset((picked.a.system, picked.b.system)))

        assert pairs == {frozenset(('medium', 'small'))}

    def test_puts_the_less_shown_order_on_the_left(self) -> None:
        pool = make_pool(systems=('large', 'small'))
        votes = votes_between(pool, 'large', 'small', ['a', 'b', 'tie']) + (
            votes_between(pool, 'small', 'large', ['a', 'b'], start=4)
        )
        rng = np.random.default_rng(0)

        picked = draw(pool, matched(pool, votes), set(), AT, rng, Board(pool.systems))

        assert picked is not None
        assert (picked.a.system, picked.b.system) == ('small', 'large')

    def test_repeats_a_vote_a_day_old_with_sides_swapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scheduler, 'REPEAT_SHARE', 1.0)
        pool = make_pool(systems=('large', 'small'))
        (original,) = votes_between(pool, 'large', 'small', ['a'])
        original = original.model_copy(update={'recorded_at': AT - timedelta(days=1)})

        picked = draw(
            pool,
            matched(pool, [original]),
            set(),
            AT,
            np.random.default_rng(0),
            Board(pool.systems),
        )

        assert picked is not None
        assert picked.repeat_of == 1
        assert (picked.case, Side.of(picked.a), Side.of(picked.b)) == (
            original.case,
            original.b,
            original.a,
        )

    @pytest.mark.parametrize(
        ('age', 'history', 'used'),
        [
            (timedelta(hours=23), lambda vote: [vote], set()),
            (
                timedelta(days=2),
                lambda vote: [
                    vote,
                    vote.model_copy(
                        update={'id': 2, 'a': vote.b, 'b': vote.a, 'repeat_of': 1}
                    ),
                ],
                set(),
            ),
            (timedelta(days=1), lambda vote: [vote], {'c0'}),
        ],
        ids=['too-recent', 'already-repeated', 'case-used'],
    )
    def test_repeats_no_vote_that_is_recent_repeated_or_on_a_used_case(
        self,
        monkeypatch: pytest.MonkeyPatch,
        age: timedelta,
        history: Callable[[Vote], list[Vote]],
        used: set[str],
    ) -> None:
        monkeypatch.setattr(scheduler, 'REPEAT_SHARE', 1.0)
        pool = make_pool(systems=('large', 'small'))
        (original,) = votes_between(pool, 'large', 'small', ['a'])
        votes = history(original.model_copy(update={'recorded_at': AT - age}))

        picked = draw(
            pool,
            matched(pool, votes),
            used,
            AT,
            np.random.default_rng(0),
            Board(pool.systems),
        )

        assert picked is not None
        assert picked.repeat_of is None


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
