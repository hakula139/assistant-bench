from dataclasses import replace

import pytest

from assistant_bench.arena.snapshots import fit_points
from assistant_bench.scoring import ranking
from assistant_bench.scoring.ranking import Comparison
from snapshot_support import make_analysis, make_evidence


class TestFitPoints:
    def test_fits_all_components_after_choosing_the_weighted_human_root(self) -> None:
        ordinary = tuple(
            make_evidence(index, weight=0.4) for index in range(250)
        ) + tuple(
            make_evidence(index + 250, actor=f'actor-{index}', systems=('x', 'y'))
            for index in range(101)
        )
        analysis = make_analysis(ordinary)

        components = fit_points(analysis)

        assert tuple(component.systems for component in components) == (
            ('x', 'y'),
            ('a', 'b'),
        )
        assert components[0].score[0] == pytest.approx(ranking.ANCHOR)
        assert components[1].score[0] == pytest.approx(ranking.ANCHOR)
        for component, rows in zip(
            components, (ordinary[250:], ordinary[:250]), strict=True
        ):
            expected = ranking.rate_point(
                ranking.Matchups.build([row.comparison for row in rows]),
                component.systems[0],
            )
            assert component.score == pytest.approx(expected.score)

    def test_keeps_settlement_mass_separate_from_human_root_selection(self) -> None:
        ordinary = (make_evidence(1, systems=('x', 'y')),)
        settled = tuple(
            Comparison('a', 'b', 1.0, f'settled-{index}', 0.25, sided=False)
            for index in range(20)
        )
        analysis = make_analysis(ordinary, settlements=settled)

        components = fit_points(analysis)

        assert components[0].systems == ('x', 'y')
        expected = ranking.rate_point(ranking.Matchups.build(settled), 'a')
        assert components[1].systems == ('a', 'b')
        assert components[1].score == pytest.approx(expected.score)

    def test_supports_settlement_only_and_unrated_startup(self) -> None:
        empty = make_analysis()
        settled = make_analysis(
            settlements=(Comparison('a', 'b', 0.0, 'settled', 0.25, sided=False),)
        )

        startup = fit_points(empty)
        components = fit_points(settled)

        assert startup == ()
        assert components[0].systems == ('a', 'b')
        assert components[0].score[0] == pytest.approx(ranking.ANCHOR)
        assert components[0].score[1] > ranking.ANCHOR

    @pytest.mark.parametrize(
        'changes',
        [
            {'l2_penalty': 0.02},
            {'points_per_logit': 200.0},
            {'anchor_score': 1200.0},
        ],
        ids=['penalty', 'scale', 'anchor'],
    )
    def test_rejects_a_frozen_policy_the_fitter_does_not_implement(
        self, changes: dict[str, float]
    ) -> None:
        analysis = make_analysis((make_evidence(1),))
        method = analysis.policy.method.model_copy(update=changes)
        different = replace(
            analysis, policy=analysis.policy.model_copy(update={'method': method})
        )

        with pytest.raises(ValueError, match='unsupported point-fit policy'):
            fit_points(different)
