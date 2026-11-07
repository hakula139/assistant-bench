from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from scipy import optimize
from statsmodels.discrete.discrete_model import Logit

from assistant_bench.scoring import ranking
from assistant_bench.scoring.ranking import (
    ANCHOR,
    Comparison,
    Matchups,
    fit,
    rank_agreement,
    rate_point,
    rate_root,
)


STRENGTHS = {'large': 0.8, 'medium': 0.3, 'small': -0.4}


class TestSplitDifference:
    def test_uses_case_clusters_and_centered_common_scores(self) -> None:
        public = [
            Comparison(
                'a', 'b', 0.0 if case % 3 == 0 else 1.0, f'public-{case}', sided=False
            )
            for case in range(30)
        ]
        held = [
            Comparison(
                'a', 'b', 1.0 if case % 3 == 0 else 0.0, f'held-{case}', sided=False
            )
            for case in range(30)
        ]

        compared = ranking.split_difference(public, held)

        result = compared.difference
        assert compared.unavailable is None and result is not None
        assert compared.cases == (30, 30)
        assert result.systems == ('a', 'b')
        assert result.difference[0] > 100
        assert result.difference.sum() == pytest.approx(0)
        assert result.half_width[0] == pytest.approx(result.half_width[1])
        assert 90 < result.half_width[0] < 105

        duplicated = ranking.split_difference(public * 100, held * 100).difference
        assert duplicated is not None
        assert duplicated.half_width == pytest.approx(result.half_width, rel=0.01)

        repeated = [row._replace(case='same') for row in held]
        assert (
            ranking.split_difference(public, repeated).unavailable
            == 'insufficient_cases'
        )
        separated = [row._replace(score=1.0) for row in held]
        assert (
            ranking.split_difference(public, separated).unavailable == 'separated_fit'
        )
        identical = ranking.split_difference(public, public).difference
        assert identical is not None
        assert identical.difference == pytest.approx([0, 0])

    @pytest.mark.parametrize('common_cases', [5, 30])
    def test_counts_only_cases_used_by_the_common_field(
        self, common_cases: int
    ) -> None:
        common = [
            Comparison('a', 'b', float(case % 2), f'common-{case}', sided=False)
            for case in range(common_cases)
        ]
        public = common + [
            Comparison('a', 'c', 0.5, f'extra-{case}') for case in range(70)
        ]
        held = common + [
            Comparison('a', 'd', 0.5, f'extra-{case}') for case in range(80)
        ]

        result = ranking.split_difference(public, held)

        assert result.cases == (common_cases, common_cases)
        assert result.unavailable == (
            'insufficient_cases' if common_cases < 30 else None
        )

    def test_keeps_available_counts_when_no_common_field_exists(self) -> None:
        public = [Comparison('a', 'b', 0.5, f'public-{case}') for case in range(30)]
        held = [Comparison('c', 'd', 0.5, f'held-{case}') for case in range(17)]

        result = ranking.split_difference(public, held)

        assert result.cases == (30, 17)
        assert result.unavailable == 'no_common_field'


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
        assert shifted.score[0] == pytest.approx(ANCHOR)
        assert np.diff(shifted.score) == pytest.approx(np.diff(ratings.score))

    def test_fits_on_one_blas_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        rate_point(Matchups.build(simulate(60)), 'large')

        assert threads == [1]


class TestRateRoot:
    ORDER = ('a', 'b', 'x', 'y')

    @pytest.mark.parametrize(
        ('reference', 'anchor'),
        [('y', 'y'), ('b', 'x')],
        ids=['reference-in-component', 'reference-elsewhere'],
    )
    def test_fits_the_weighted_human_roots_component(
        self, reference: str, anchor: str
    ) -> None:
        shared = [Comparison('a', 'b', 1.0, f'ab-{i}', 0.4) for i in range(250)]
        many = [Comparison('x', 'y', 1.0, f'xy-{i}') for i in range(101)]

        component = rate_root(shared + many, self.ORDER, reference)

        assert (component.ratings.systems, component.root, component.anchor) == (
            ('x', 'y'),
            'x',
            anchor,
        )
        expected = rate_point(Matchups.build(many), anchor)
        assert component.ratings.score == pytest.approx(expected.score)

    def test_keeps_settlement_mass_out_of_root_selection(self) -> None:
        settled = [
            Comparison('a', 'b', 1.0, f'settled-{i}', 0.25, sided=False)
            for i in range(20)
        ]

        component = rate_root(
            [Comparison('x', 'y', 1.0, 'voted'), *settled], self.ORDER, 'a'
        )

        assert component.ratings.systems == ('x', 'y')

    def test_supports_settlement_only_comparisons(self) -> None:
        settled = [Comparison('a', 'b', 0.0, 'settled', 0.25, sided=False)]

        component = rate_root(settled, self.ORDER, 'a')

        assert component.ratings.systems == ('a', 'b')
        assert component.ratings.score[0] == pytest.approx(ANCHOR)
        assert component.ratings.score[1] > ANCHOR


class TestUncertainty:
    TRUE = np.linspace(-0.9, 0.9, 7)
    CASE_EFFECT = 1.0

    def pseudo_true(self) -> npt.NDArray[np.float64]:
        """Return the centered strengths the fit reaches over new cases."""
        nodes, weights = np.polynomial.hermite_e.hermegauss(40)
        spread = np.sqrt(2) * self.CASE_EFFECT * nodes
        k = len(self.TRUE)
        expected = [
            Comparison(
                f's{i}',
                f's{j}',
                float(
                    weights @ (1 / (1 + np.exp(self.TRUE[j] - self.TRUE[i] - spread)))
                )
                / weights.sum(),
                'population',
                1e6,
                sided=False,
            )
            for i in range(k)
            for j in range(k)
            if i != j
        ]
        return fit(Matchups.build(expected)).strength

    def round(self, rng: np.random.Generator) -> list[Comparison]:
        k = len(self.TRUE)
        effects = rng.normal(0, self.CASE_EFFECT, (30, k))
        comparisons: list[Comparison] = []
        for _ in range(1000):
            i, j = rng.choice(k, size=2, replace=False)
            case = rng.integers(30)
            gap = self.TRUE[i] - self.TRUE[j] + effects[case, i] - effects[case, j]
            p = 1 / (1 + np.exp(-gap))
            comparisons.append(
                Comparison(f's{i}', f's{j}', float(rng.random() < p), f'case-{case}')
            )
        return comparisons

    def test_covers_the_strengths_over_new_cases_at_the_interval_level(self) -> None:
        truth = self.pseudo_true()
        rng = np.random.default_rng(7)

        covered: list[npt.NDArray[np.bool_]] = []
        for _ in range(400):
            component = rate_root(self.round(rng), (), 's0')
            assert component.uncertainty is not None
            score = component.ratings.score
            estimate = (score - score.mean()) / ranking.ELO_PER_LOGIT
            half = component.uncertainty.half_widths() / ranking.ELO_PER_LOGIT
            covered.append(np.abs(estimate - truth) <= half)

        assert 0.93 <= np.mean(covered) <= 0.97

    def test_widens_every_interval_when_votes_share_their_cases(self) -> None:
        votes = simulate(400, seed=3)
        independent = rate_root(
            [vote._replace(case=f'case-{i}') for i, vote in enumerate(votes)],
            (),
            'medium',
        )
        assert independent.uncertainty is not None

        shared = rate_root(
            [vote._replace(case=f'{vote.left}-{vote.score}') for vote in votes],
            (),
            'medium',
        )

        assert shared.uncertainty is not None
        assert shared.cases == 9
        widths = independent.uncertainty.half_widths()
        assert widths[independent.ratings.systems.index('medium')] > 0
        assert np.all(shared.uncertainty.half_widths() > 2 * widths)

    @pytest.mark.parametrize(
        'votes',
        [
            pytest.param(
                [vote._replace(case='case') for vote in simulate(100)], id='one-case'
            ),
            pytest.param(
                [vote._replace(score=1.0) for vote in simulate(200)], id='one-pane'
            ),
            pytest.param(
                [
                    vote._replace(score=float(vote.left == 'large'))
                    if 'large' in (vote.left, vote.right)
                    else vote
                    for vote in simulate(200)
                ],
                id='all-wins-entrant',
            ),
        ],
    )
    def test_has_no_uncertainty_without_a_clustered_variance(
        self, votes: list[Comparison]
    ) -> None:
        component = rate_root(votes, (), 'medium')

        assert component.uncertainty is None

    @pytest.mark.parametrize(
        ('other', 'separated'),
        [('small', True), ('medium', False)],
        ids=['far-behind', 'close-behind'],
    )
    def test_separates_the_leader_only_from_a_clear_gap(
        self, other: str, separated: bool
    ) -> None:
        component = rate_root(simulate(60), (), 'medium')
        assert component.uncertainty is not None

        result = component.uncertainty.separates(component.ratings, 'large', other)

        assert result is separated


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
