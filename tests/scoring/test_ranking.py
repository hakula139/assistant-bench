from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from scipy import optimize
from statsmodels.discrete.discrete_model import Logit

from assistant_bench.scoring import ranking
from assistant_bench.scoring.ranking import (
    ANCHOR,
    ELO_PER_LOGIT,
    Comparison,
    Matchups,
    fit,
    rank_agreement,
    rate_components,
    rate_point,
)


STRENGTHS = {'large': 0.8, 'medium': 0.3, 'small': -0.4}


def simulate(
    n: int, seed: int = 0, position: float = 0.25, cases: int = 40
) -> list[Comparison]:
    """Draw votes from Bradley-Terry with a left-side advantage and 20% ties."""
    rng = np.random.default_rng(seed)
    names = list(STRENGTHS)
    comparisons: list[Comparison] = []
    for _ in range(n):
        left, right = rng.choice(names, size=2, replace=False)
        p = 1 / (1 + np.exp(-(STRENGTHS[left] - STRENGTHS[right] + position)))
        score = 0.5 if rng.random() < 0.2 else float(rng.random() < p)
        case = f'case-{rng.integers(cases)}'
        comparisons.append(Comparison(str(left), str(right), score, case))
    return comparisons


class TestMatchups:
    class TestBuild:
        def test_indexes_systems_by_name(self) -> None:
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

    class TestRoot:
        @pytest.mark.parametrize(
            ('comparisons', 'order', 'root'),
            [
                (
                    [('c', 'a', True), ('c', 'b', True)] * 2 + [('a', 'b', True)],
                    ['a', 'b', 'c'],
                    'c',
                ),
                ([('x', 'y', True)], ['y', 'x'], 'y'),
                ([('y', 'x', True)], [], 'x'),
                (
                    [('a', 'b', True), ('a', 'c', True)] + [('b', 'c', False)] * 5,
                    ['b', 'c', 'a'],
                    'a',
                ),
            ],
            ids=['most-voted', 'order-breaks-a-tie', 'name-breaks-a-tie', 'votes-only'],
        )
        def test_roots_at_the_most_voted_system(
            self,
            comparisons: list[tuple[str, str, bool]],
            order: list[str],
            root: str,
        ) -> None:
            matchups = Matchups.build(
                [
                    Comparison(left, right, 1.0, 'c', 1.0 if sided else 0.25, sided)
                    for left, right, sided in comparisons
                ]
            )

            chosen = matchups.root(order)

            assert chosen == root


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

    @pytest.mark.parametrize(
        'covariate',
        [
            pytest.param([0.0] * 9, id='zero'),
            pytest.param([1.0] * 9, id='position'),
            pytest.param([1e16] * 9, id='scale-dependent-rank-tolerance'),
            pytest.param([1.0, -1.0] * 4 + [1.0], id='system-contrast'),
            pytest.param([1.0, -1.0] * 4 + [7.0], id='zero-weight-variation'),
        ],
    )
    def test_omits_a_covariate_confounded_with_the_weighted_base_design(
        self, covariate: list[float]
    ) -> None:
        comparisons = [
            Comparison('large', 'small', 0.75, 'a'),
            Comparison('small', 'large', 0.25, 'a'),
        ] * 4 + [Comparison('large', 'small', 0.5, 'ignored', weight=0)]
        matchups = Matchups.build(comparisons)
        base = fit(matchups)

        result = fit(matchups, np.array(covariate))

        assert result.covariate is None
        assert result.strength == pytest.approx(base.strength)
        assert result.position == pytest.approx(base.position)

    def test_raises_when_the_loss_cannot_be_minimized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        matchups = Matchups.build(
            [
                Comparison('large', 'small', 1.0, 'a'),
                Comparison('small', 'large', 1.0, 'a'),
            ]
        )
        failure = SimpleNamespace(success=False, message='iteration limit')

        def minimize(*args: object, **kwargs: object) -> SimpleNamespace:
            return failure

        monkeypatch.setattr(optimize, 'minimize', minimize)

        with pytest.raises(RuntimeError, match='did not converge: iteration limit'):
            fit(matchups)


class TestRatePoint:
    def test_anchors_the_named_system_and_preserves_score_gaps(self) -> None:
        matchups = Matchups.build(simulate(600))

        ratings = rate_point(matchups, 'medium')
        shifted = rate_point(matchups, 'large')

        assert ratings.systems == ('large', 'medium', 'small')
        assert ratings.score[1] == pytest.approx(ANCHOR)
        assert ratings.score[0] > ANCHOR > ratings.score[2]
        assert ratings.position == pytest.approx(0.25 * ELO_PER_LOGIT, abs=40)
        assert shifted.score[0] == pytest.approx(ANCHOR)
        assert np.diff(shifted.score) == pytest.approx(np.diff(ratings.score))
        assert shifted.position == ratings.position

    def test_fits_on_one_blas_thread_and_restores_the_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        thread_limit = 8
        threads: list[int] = []
        minimize = optimize.minimize

        @contextmanager
        def limit_threads(*, limits: int, user_api: str) -> Generator[None]:
            nonlocal thread_limit
            assert user_api == 'blas'
            previous, thread_limit = thread_limit, limits
            try:
                yield
            finally:
                thread_limit = previous

        def recording(*args: Any, **kwargs: Any) -> Any:
            threads.append(thread_limit)
            return minimize(*args, **kwargs)

        monkeypatch.setattr(ranking, 'threadpool_limits', limit_threads)
        monkeypatch.setattr(optimize, 'minimize', recording)

        with limit_threads(limits=2, user_api='blas'):
            rate_point(Matchups.build(simulate(60)), 'large')
            after = thread_limit

        assert threads == [1]
        assert after == 2
        assert thread_limit == 8


class TestRateComponents:
    ORDER = ('a', 'b', 'x', 'y')

    def test_fits_every_component_after_the_weighted_human_root(self) -> None:
        shared = [Comparison('a', 'b', 1.0, f'ab-{i}', 0.4) for i in range(250)]
        many = [Comparison('x', 'y', 1.0, f'xy-{i}') for i in range(101)]

        components = rate_components(shared + many, self.ORDER, 'b')

        assert [(c.ratings.systems, c.root, c.anchor) for c in components] == [
            (('x', 'y'), 'x', 'x'),
            (('a', 'b'), 'a', 'b'),
        ]
        for component, rows in zip(components, (many, shared), strict=True):
            expected = rate_point(Matchups.build(rows), component.anchor)
            assert component.ratings.score == pytest.approx(expected.score)

    def test_keeps_settlement_mass_out_of_root_selection(self) -> None:
        settled = [
            Comparison('a', 'b', 1.0, f'settled-{i}', 0.25, sided=False)
            for i in range(20)
        ]

        components = rate_components(
            [Comparison('x', 'y', 1.0, 'voted'), *settled], self.ORDER, 'a'
        )

        assert [c.ratings.systems for c in components] == [('x', 'y'), ('a', 'b')]
        expected = rate_point(Matchups.build(settled), 'a')
        assert components[1].ratings.score == pytest.approx(expected.score)

    def test_supports_settlement_only_and_unrated_startup(self) -> None:
        settled = [Comparison('a', 'b', 0.0, 'settled', 0.25, sided=False)]

        startup = rate_components([], self.ORDER, 'a')
        (component,) = rate_components(settled, self.ORDER, 'a')

        assert startup == ()
        assert component.ratings.systems == ('a', 'b')
        assert component.ratings.score[0] == pytest.approx(ANCHOR)
        assert component.ratings.score[1] > ANCHOR


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
