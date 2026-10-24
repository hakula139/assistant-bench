from collections import defaultdict

import pytest

from assistant_bench.scoring.weighting import actor_weights


class TestActorWeights:
    @pytest.mark.parametrize(
        ('counts', 'expected'),
        [
            ((), ()),
            ((99,), (1.0,)),
            ((100,), (1.0,)),
            ((101,), (100 / 101,)),
            ((250,), (0.4,)),
            ((250, 25), (0.4, 1.0)),
            ((251, 100, 101), (100 / 251, 1.0, 100 / 101)),
        ],
        ids=['empty', 'below', 'at', 'above', 'large', 'mixed', 'rebalanced'],
    )
    def test_shares_each_actor_budget_across_every_eligible_choice(
        self, counts: tuple[int, ...], expected: tuple[float, ...]
    ) -> None:
        actors = tuple(
            f'actor-{actor}' for actor, count in enumerate(counts) for _ in range(count)
        )
        aligned = tuple(
            weight
            for count, weight in zip(counts, expected, strict=True)
            for _ in range(count)
        )

        weights = actor_weights(actors, 100)

        assert weights == pytest.approx(aligned)
        assert sum(weights) == pytest.approx(sum(min(count, 100) for count in counts))

    def test_preserves_alignment_and_budgets_after_input_permutation(self) -> None:
        actors = tuple(['frequent'] * 250 + ['occasional'] * 25 + ['single'])
        permutation = tuple(range(len(actors) - 1, -1, -2)) + tuple(
            range(len(actors) - 2, -1, -2)
        )
        reordered = tuple(actors[index] for index in permutation)

        original = actor_weights(actors, 100)
        changed = actor_weights(reordered, 100)

        assert changed == tuple(original[index] for index in permutation)
        totals: defaultdict[str, float] = defaultdict(float)
        for actor, weight in zip(reordered, changed, strict=True):
            totals[actor] += weight

        assert totals == pytest.approx(
            {'frequent': 100.0, 'occasional': 25.0, 'single': 1.0}
        )

    def test_uses_the_recorded_cap(self) -> None:
        actors = ('owner',) * 50

        weights = actor_weights(actors, 20)

        assert weights == (0.4,) * 50
