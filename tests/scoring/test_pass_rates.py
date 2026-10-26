import numpy as np
import pytest
from scipy import stats

from assistant_bench.scoring.pass_rates import clopper_pearson, separates


class TestClopperPearson:
    @pytest.mark.parametrize(
        ('passes', 'tasks'), [(0, 10), (6, 6), (5, 6), (1, 2), (15, 40)]
    )
    def test_matches_the_exact_binomial_interval_for_whole_counts(
        self, passes: int, tasks: int
    ) -> None:
        expected = stats.binomtest(passes, tasks).proportion_ci(method='exact')

        interval = clopper_pearson(passes / tasks, tasks)

        assert interval == pytest.approx((expected.low, expected.high), abs=1e-12)

    @pytest.mark.parametrize(('passes', 'tasks'), [(0.5, 6), (5.5, 6), (2.25, 7)])
    def test_bounds_fractional_counts_between_their_whole_neighbors(
        self, passes: float, tasks: int
    ) -> None:
        below = clopper_pearson(int(passes) / tasks, tasks)
        above = clopper_pearson((int(passes) + 1) / tasks, tasks)

        lower, upper = clopper_pearson(passes / tasks, tasks)

        assert below[0] < lower < above[0]
        assert below[1] < upper < above[1]

    @pytest.mark.parametrize(
        ('tasks', 'samples', 'rate'),
        [(6, 3, 0.8), (6, 1, 0.97), (6, 3, 0.97), (12, 1, 0.99)],
    )
    def test_covers_task_clustered_rates_at_small_task_counts(
        self, tasks: int, samples: int, rate: float
    ) -> None:
        rng = np.random.default_rng(0)
        difficulty = rng.beta(0.5 * rate, 0.5 * (1 - rate), size=(4000, tasks))
        shares = rng.binomial(samples, difficulty).mean(axis=1) / samples

        bounds = [clopper_pearson(float(share), tasks) for share in shares]
        covered = np.mean([lower <= rate <= upper for lower, upper in bounds])

        assert covered >= 0.95


class TestSeparates:
    @pytest.mark.parametrize(
        ('leader', 'other', 'expected'),
        [
            ([1.0] * 6, [0.0] * 6, True),
            ([1.0] * 6 + [0.5] * 3, [0.0] * 6 + [0.5] * 3, True),
            ([1.0] * 5 + [0.0], [0.0] * 5 + [1.0], False),
            ([1.0] * 5, [0.0] * 5, False),
            ([0.5] * 6, [0.5] * 6, False),
            ([0.0] * 6, [1.0] * 6, False),
        ],
        ids=[
            'six-wins',
            'ignores-agreeing-tasks',
            'one-loss-in-six',
            'five-wins-only',
            'no-discordant-task',
            'other-wins',
        ],
    )
    def test_requires_an_exact_sign_test_in_the_leaders_favor(
        self, leader: list[float], other: list[float], expected: bool
    ) -> None:
        assert separates(leader, other) is expected
