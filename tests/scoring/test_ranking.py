from typing import Any

import numpy as np
import pytest
from scipy import optimize
from statsmodels.discrete.discrete_model import Logit
from threadpoolctl import threadpool_info, threadpool_limits

from assistant_bench.scoring.ranking import (
    ANCHOR,
    ELO_PER_LOGIT,
    Comparison,
    Matchups,
    Ratings,
    bootstrap,
    fit,
    rank_agreement,
    rank_spread,
    rate,
    separability,
    ties,
)


STRENGTHS = {'large': 0.8, 'medium': 0.3, 'small': -0.4}


def simulate(
    n: int, seed: int = 0, position: float = 0.25, cases: int = 40
) -> list[Comparison]:
    """Draw votes from Bradley-Terry with a left-side advantage and 20% ties."""
    rng = np.random.default_rng(seed)
    names = list(STRENGTHS)
    comparisons = []
    for _ in range(n):
        left, right = rng.choice(names, size=2, replace=False)
        p = 1 / (1 + np.exp(-(STRENGTHS[left] - STRENGTHS[right] + position)))
        score = 0.5 if rng.random() < 0.2 else float(rng.random() < p)
        case = f'case-{rng.integers(cases)}'
        comparisons.append(Comparison(str(left), str(right), score, case))
    return comparisons


def blas_threads() -> set[int]:
    return {
        lib['num_threads'] for lib in threadpool_info() if lib['user_api'] == 'blas'
    }


class TestMatchups:
    class TestBuild:
        def test_indexes_systems_by_name_and_cases_by_appearance(self) -> None:
            matchups = Matchups.build(
                [
                    Comparison('small', 'large', 1.0, 'b'),
                    Comparison('large', 'medium', 0.5, 'a'),
                    Comparison('medium', 'small', 0.0, 'b'),
                ]
            )

            assert matchups.systems == ('large', 'medium', 'small')
            assert matchups.left.tolist() == [2, 0, 1]
            assert matchups.right.tolist() == [0, 1, 2]
            assert matchups.score.tolist() == [1.0, 0.5, 0.0]
            assert matchups.cluster.tolist() == [0, 1, 0]

    class TestComponent:
        def test_keeps_the_systems_and_rows_connected_to_a_system(self) -> None:
            matchups = Matchups.build(
                [
                    Comparison('a', 'b', 1.0, 'x'),
                    Comparison('c', 'd', 0.0, 'y'),
                    Comparison('b', 'a', 0.5, 'z', sided=False),
                ]
            )

            connected = matchups.component('b')

            assert connected.systems == ('a', 'b')
            assert connected.left.tolist() == [0, 1]
            assert connected.right.tolist() == [1, 0]
            assert connected.score.tolist() == [1.0, 0.5]
            assert connected.sided.tolist() == [1.0, 0.0]
            assert connected.cluster.tolist() == [0, 1]


class TestFit:
    def test_matches_a_fractional_logit_from_statsmodels(self) -> None:
        comparisons = simulate(3000)
        matchups = Matchups.build(comparisons)
        covariate = np.random.default_rng(1).normal(size=len(comparisons))

        result = fit(matchups, covariate)

        k = len(matchups.systems)
        design = np.zeros((len(comparisons), k))
        design[np.arange(len(comparisons)), matchups.left] = 1
        design[np.arange(len(comparisons)), matchups.right] = -1
        exog = np.column_stack([design[:, 1:], np.ones(len(comparisons)), covariate])
        reference = Logit(matchups.score, exog).fit(disp=False).params
        gaps = result.strength[1:] - result.strength[0]
        assert gaps == pytest.approx(reference[: k - 1], abs=2e-3)
        assert result.position == pytest.approx(reference[k - 1], abs=2e-3)
        assert result.covariate == pytest.approx(reference[k], abs=2e-3)

    def test_leaves_settled_rows_out_of_the_position_term(self) -> None:
        votes = simulate(600)
        settled = [
            Comparison(left, right, 0.5, f'settled-{i}', sided=False)
            for i, (left, right) in enumerate(
                [('large', 'medium'), ('medium', 'small'), ('small', 'large')] * 80
            )
        ]

        alone, beside = (
            fit(Matchups.build(comparisons)) for comparisons in (votes, votes + settled)
        )

        assert beside.position == pytest.approx(alone.position, abs=0.02)

    def test_keeps_a_system_that_wins_every_vote_finite(self) -> None:
        comparisons = [
            Comparison('large', 'small', 1.0, f'case-{i}') for i in range(20)
        ] + [Comparison('small', 'large', 0.0, f'case-{i}') for i in range(20)]

        result = fit(Matchups.build(comparisons))

        assert np.isfinite(result.strength).all()
        assert 6 < result.strength[0] - result.strength[1] < 8

    def test_raises_when_the_loss_cannot_be_minimized(self) -> None:
        matchups = Matchups.build(
            [
                Comparison('large', 'small', 1.0, 'a'),
                Comparison('small', 'large', 1.0, 'a'),
            ]
        )

        with (
            np.errstate(invalid='ignore'),
            pytest.raises(RuntimeError, match='did not converge'),
        ):
            fit(matchups, np.array([np.nan, 1.0]))


class TestBootstrap:
    def test_resamples_whole_cases(self) -> None:
        comparisons = [
            Comparison('large', 'small', score, 'only')
            for score in (1.0, 1.0, 0.0, 0.5, 1.0, 0.0)
        ]

        strengths = bootstrap(Matchups.build(comparisons), 'large', resamples=20)

        assert strengths.shape == (20, 2)
        assert np.ptp(strengths, axis=0) == pytest.approx([0, 0], abs=1e-6)

    def test_marks_a_system_a_resample_leaves_unconnected(self) -> None:
        comparisons = [
            Comparison('large', 'small', 1.0, 'shared'),
            Comparison('small', 'large', 0.0, 'shared'),
            Comparison('new', 'large', 1.0, 'own'),
        ]

        strengths = bootstrap(Matchups.build(comparisons), 'small', resamples=50)

        large, new, small = np.isnan(strengths).T
        assert not small.any()
        assert 0 < large.sum() < new.sum() < 50
        assert not (large & ~new).any()


class TestRate:
    def test_anchors_the_reference_and_brackets_each_score(self) -> None:
        matchups = Matchups.build(simulate(600))

        ratings = rate(matchups, 'medium', resamples=200)

        assert ratings.systems == ('large', 'medium', 'small')
        assert ratings.score[1] == pytest.approx(ANCHOR)
        assert ratings.score[0] > ANCHOR > ratings.score[2]
        assert (ratings.lower < ratings.score).all()
        assert (ratings.score < ratings.upper).all()
        assert ratings.position == pytest.approx(0.25 * ELO_PER_LOGIT, abs=40)

    def test_writes_the_same_intervals_for_the_same_votes(self) -> None:
        matchups = Matchups.build(simulate(200))

        first, second = (rate(matchups, 'large', resamples=50) for _ in range(2))

        assert first.lower.tolist() == second.lower.tolist()
        assert first.upper.tolist() == second.upper.tolist()

    def test_leaves_a_record_on_few_cases_unbounded(self) -> None:
        challenger = [
            Comparison('new', opponent, 1.0, f'challenger-{i % 2}')
            for i, opponent in enumerate(['large', 'medium', 'small'] * 3)
        ][:8]

        alone = rate(Matchups.build(simulate(600)), 'medium')
        ratings = rate(Matchups.build(simulate(600) + challenger), 'medium')

        new = ratings.systems.index('new')
        assert (ratings.lower[new], ratings.upper[new]) == (-np.inf, np.inf)
        bounded = ratings.bounded()
        assert bounded.systems == alone.systems
        assert bounded.lower == pytest.approx(alone.lower, abs=50)
        assert bounded.upper == pytest.approx(alone.upper, abs=50)

    def test_fits_on_one_blas_thread_and_restores_the_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        threads: list[set[int]] = []
        minimize = optimize.minimize

        def recording(*args: Any, **kwargs: Any) -> Any:
            threads.append(blas_threads())
            return minimize(*args, **kwargs)

        monkeypatch.setattr(optimize, 'minimize', recording)

        with threadpool_limits(limits=2, user_api='blas'):
            rate(Matchups.build(simulate(60)), 'large', resamples=3)
            after = blas_threads()

        assert threads == [{1}] * 5
        assert after == {2}


class TestTies:
    def test_keeps_the_candidates_whose_paired_difference_spans_zero(self) -> None:
        steps = np.linspace(-1, 1, 201)
        draws = np.stack(
            [1100 + 0 * steps, 1060 + 100 * steps, 1000 + 0 * steps, 1300 + 0 * steps],
            axis=1,
        )
        ratings = Ratings(
            systems=('top', 'close', 'far', 'excluded'),
            score=np.array([1100.0, 1060.0, 1000.0, 1300.0]),
            lower=draws.min(axis=0),
            upper=draws.max(axis=0),
            draws=draws,
            position=0.0,
        )

        assert ties(ratings, [0, 1, 2]) == [0, 1]

    def test_counts_an_unbounded_difference_as_a_tie(self) -> None:
        draws = np.array([[1100.0, 900.0]] * 10 + [[1100.0, np.nan]] * 2)
        ratings = Ratings(
            systems=('top', 'detached'),
            score=np.array([1100.0, 900.0]),
            lower=np.array([1100.0, -np.inf]),
            upper=np.array([1100.0, np.inf]),
            draws=draws,
            position=0.0,
        )

        assert ties(ratings, [0, 1]) == [0, 1]


class TestRankSpread:
    def test_spans_the_ranks_each_interval_allows(self) -> None:
        lower = np.array([1100.0, 1000.0, 980.0, 800.0])
        upper = np.array([1200.0, 1110.0, 1050.0, 900.0])

        best, worst = rank_spread(lower, upper)

        assert best.tolist() == [1, 1, 2, 4]
        assert worst.tolist() == [2, 3, 3, 4]


class TestSeparability:
    def test_counts_the_pairs_whose_intervals_do_not_overlap(self) -> None:
        lower = np.array([1100.0, 1000.0, 800.0])
        upper = np.array([1200.0, 1110.0, 900.0])

        assert separability(lower, upper) == pytest.approx(2 / 3)

    def test_is_undefined_for_one_system(self) -> None:
        assert separability(np.array([1.0]), np.array([2.0])) is None


class TestRankAgreement:
    @pytest.mark.parametrize(
        ('second', 'expected'),
        [([3.0, 2.0, 1.0], 1.0), ([1.0, 2.0, 3.0], -1.0)],
        ids=['same', 'reversed'],
    )
    def test_compares_two_orders(self, second: list[float], expected: float) -> None:
        tau = rank_agreement(np.array([3.0, 2.0, 1.0]), np.array(second))

        assert tau == pytest.approx(expected)

    def test_is_undefined_below_two_systems(self) -> None:
        assert rank_agreement(np.array([1.0]), np.array([1.0])) is None
