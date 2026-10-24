import math
import random
from collections import Counter
from itertools import permutations

import pytest

from assistant_bench_arena.ledger_models import Output, Point
from assistant_bench_arena.read_models import ClaimState, Coverage, DueRepeat
from assistant_bench_arena.releases import (
    EvaluationBundle,
    Release,
    evaluation_identity,
    point_snapshot,
    settlements,
)
from assistant_bench_arena.scheduler import CandidatePool, pair_log_weights
from assistant_bench_arena.serialization import comparison_identity, digest
from assistant_bench_arena.snapshots import OutputContentApproval
from ledger_fixtures import case_key, make_release, output_key


def evaluation_with(release: Release, outputs: tuple[Output, ...]) -> EvaluationBundle:
    numerics = settlements(outputs)
    evaluation = release.evaluation.model_copy(
        update={
            'id': evaluation_identity(
                release.evaluation.manifest,
                release.cases,
                release.systems,
                outputs,
                numerics,
            ),
            'outputs': len(outputs),
            'numerics': len(numerics),
        }
    )

    return EvaluationBundle(
        policy=release.policy,
        evaluation=evaluation,
        cases=release.cases,
        systems=release.systems,
        outputs=outputs,
        numerics=numerics,
    )


def sample(output: Output, index: int, *, content_digest: str | None = None) -> Output:
    source = digest({'source': output.metadata.source_digest, 'sample': index})
    content = content_digest or digest(
        {'content': output.content_digest, 'sample': index}
    )
    key = digest({'source_digest': source, 'content_digest': content})

    return output.model_copy(
        update={
            'key': key,
            'content_digest': content,
            'metadata': output.metadata.model_copy(
                update={
                    'source_digest': source,
                    'approval': OutputContentApproval(
                        output_key=key, content_digest=content
                    ),
                }
            ),
        }
    )


class TestCandidatePool:
    class TestDraw:
        def test_seeds_every_ordered_system_pair_with_equal_coverage(self) -> None:
            names = ('large', 'medium', 'small')
            release = make_release(cases=1, systems=names)
            systems = {output.key: output.system_id for output in release.outputs}
            coverage: Counter[tuple[str, str]] = Counter()
            rng = random.Random(0)
            pool = CandidatePool.build(release, (), (), ())

            for expected in (1, 2):
                for _ in range(6):
                    rows = tuple(
                        Coverage(a_system=a, b_system=b, count=count)
                        for (a, b), count in coverage.items()
                    )
                    picked = pool.draw(
                        release.points,
                        rows,
                        release.policy.method.points_per_logit,
                        rng,
                    )
                    assert picked is not None
                    coverage[systems[picked.a_output], systems[picked.b_output]] += 1

                assert coverage == dict.fromkeys(permutations(names, 2), expected)

        @pytest.mark.parametrize('settled', ['fail', 'ungraded', 'identical', 'used'])
        def test_stops_when_the_inventory_has_no_unresolved_unused_pair(
            self, settled: str
        ) -> None:
            release = make_release(
                cases=1, failing_case=0 if settled == 'fail' else None
            )
            outputs = release.outputs

            if settled == 'ungraded':
                outputs = tuple(
                    output.model_copy(update={'verdict': 'ungraded'})
                    if output.system_id == 'large'
                    else output
                    for output in outputs
                )
            elif settled == 'identical':
                outputs = tuple(
                    sample(output, index, content_digest=digest('Identical output.'))
                    for index, output in enumerate(outputs)
                )

            evaluation = evaluation_with(release, outputs)
            points = point_snapshot(
                evaluation.evaluation.id,
                evaluation.evaluation.policy_id,
                release.points.points,
            )
            used: set[str] = {case_key(0)} if settled == 'used' else set()

            pool = CandidatePool.build(evaluation, (), used, ())

            picked = pool.draw(
                points, (), evaluation.policy.method.points_per_logit, random.Random(0)
            )

            assert pool.remaining == 0
            assert picked is None

        def test_draws_a_current_passing_case_with_distinct_content(self) -> None:
            release = make_release(cases=3, failing_case=0)
            outputs = {output.key: output for output in release.outputs}

            pool = CandidatePool.build(release, (), {case_key(1)}, ())

            picked = pool.draw(
                release.points,
                (),
                release.policy.method.points_per_logit,
                random.Random(0),
            )

            assert pool.remaining == 1
            assert picked is not None
            assert picked.case_key == case_key(2)
            assert outputs[picked.a_output].verdict == 'pass'
            assert outputs[picked.b_output].verdict == 'pass'
            assert (
                outputs[picked.a_output].system_id != outputs[picked.b_output].system_id
            )
            assert (
                outputs[picked.a_output].content_digest
                != outputs[picked.b_output].content_digest
            )

        @pytest.mark.parametrize('eligible', [False, True])
        def test_reuses_only_an_unselected_claim_at_its_current_version(
            self, eligible: bool
        ) -> None:
            release = make_release(cases=1)
            claim = ClaimState(
                id='claim',
                kind='ordinary',
                comparison_key=comparison_identity(case_key(0), 'large', 'small'),
                repeat_of=None,
                version=7,
                selected_receipt=12 if eligible else None,
                current_lease=None,
                eligible=eligible,
            )

            pool = CandidatePool.build(release, (claim,), (), ())

            picked = pool.draw(
                release.points,
                (),
                release.policy.method.points_per_logit,
                random.Random(0),
            )

            if eligible:
                assert pool.remaining == 0
                assert picked is None
            else:
                assert pool.remaining == 1
                assert picked is not None
                assert picked.claim_id == 'claim'
                assert picked.claim_version == 7
                assert {picked.a_output, picked.b_output} == {
                    output_key(0, 'large'),
                    output_key(0, 'small'),
                }

        def test_seeds_the_least_covered_orientation_before_adaptive_sampling(
            self,
        ) -> None:
            release = make_release(cases=1)
            coverage = (
                Coverage(a_system='large', b_system='small', count=2),
                Coverage(a_system='small', b_system='large', count=1),
            )

            pool = CandidatePool.build(release, (), (), ())

            picked = pool.draw(
                release.points,
                coverage,
                release.policy.method.points_per_logit,
                random.Random(0),
            )

            assert picked is not None
            assert picked.a_output == output_key(0, 'small')
            assert picked.b_output == output_key(0, 'large')

        def test_uses_the_least_covered_orientation_after_both_sides_are_seeded(
            self,
        ) -> None:
            release = make_release(cases=1)
            coverage = (
                Coverage(a_system='large', b_system='small', count=9),
                Coverage(a_system='small', b_system='large', count=2),
            )

            pool = CandidatePool.build(release, (), (), ())

            picked = pool.draw(
                release.points,
                coverage,
                release.policy.method.points_per_logit,
                random.Random(0),
            )

            assert picked is not None
            assert picked.a_output == output_key(0, 'small')
            assert picked.b_output == output_key(0, 'large')

        @pytest.mark.parametrize('gap', [10_000.0, 1e308])
        @pytest.mark.parametrize(
            'scale', [1.0, 100.0, math.ulp(0.0)], ids=['one', 'hundred', 'smallest']
        )
        def test_can_draw_after_large_finite_score_separation(
            self, gap: float, scale: float
        ) -> None:
            release = make_release(cases=1)
            points = point_snapshot(
                release.evaluation.id,
                release.evaluation.policy_id,
                {
                    'large': Point(score=gap, component='connected'),
                    'small': Point(score=-gap, component='connected'),
                },
            )
            coverage = (
                Coverage(a_system='large', b_system='small', count=2),
                Coverage(a_system='small', b_system='large', count=2),
            )

            pool = CandidatePool.build(release, (), (), ())

            picked = pool.draw(points, coverage, scale, random.Random(0))

            assert picked is not None
            assert {picked.a_output, picked.b_output} == {
                output_key(0, 'large'),
                output_key(0, 'small'),
            }

        @pytest.mark.parametrize(
            'scale', [1.0, 100.0, math.ulp(0.0)], ids=['one', 'hundred', 'smallest']
        )
        def test_selects_the_closest_huge_same_sign_scores_after_seeding(
            self, scale: float
        ) -> None:
            names = ('large', 'medium', 'small')
            release = make_release(cases=1, systems=names)
            points = point_snapshot(
                release.evaluation.id,
                release.evaluation.policy_id,
                {
                    name: Point(score=score, component='connected')
                    for name, score in zip(
                        names,
                        (1e308, math.nextafter(1e308, math.inf), -1e308),
                        strict=True,
                    )
                },
            )
            coverage = tuple(
                Coverage(a_system=a, b_system=b, count=2)
                for a, b in permutations(names, 2)
            )
            systems = {output.key: output.system_id for output in release.outputs}
            pool = CandidatePool.build(release, (), (), ())

            picked = pool.draw(points, coverage, scale, random.Random(0))

            assert picked is not None
            assert {systems[picked.a_output], systems[picked.b_output]} == {
                'large',
                'medium',
            }

        @pytest.mark.parametrize(
            ('gap', 'other_coverage'),
            [(1_000.0, 2), (None, 100)],
            ids=['comparable-points', 'raw-coverage'],
        )
        def test_favors_the_most_informative_unordered_pair_after_seeding(
            self, gap: float | None, other_coverage: int
        ) -> None:
            names = ('large', 'medium', 'small')
            release = make_release(cases=1, systems=names)
            points = point_snapshot(
                release.evaluation.id,
                release.evaluation.policy_id,
                {
                    name: Point(
                        score=gap if name == 'small' else 0.0,
                        component='connected',
                    )
                    if gap is not None
                    else Point(score=None, component=None)
                    for name in names
                },
            )
            coverage = tuple(
                Coverage(
                    a_system=a,
                    b_system=b,
                    count=2 if {a, b} == {'large', 'medium'} else other_coverage,
                )
                for a, b in permutations(names, 2)
            )
            systems = {output.key: output.system_id for output in release.outputs}
            selected: Counter[frozenset[str]] = Counter()
            rng = random.Random(0)
            pool = CandidatePool.build(release, (), (), ())

            for _ in range(400):
                picked = pool.draw(
                    points, coverage, release.policy.method.points_per_logit, rng
                )
                assert picked is not None
                selected[
                    frozenset((systems[picked.a_output], systems[picked.b_output]))
                ] += 1

            assert selected[frozenset(('large', 'medium'))] >= 350

        def test_samples_cases_equally_despite_different_output_sample_counts(
            self,
        ) -> None:
            release = make_release(cases=2)
            original = next(
                output
                for output in release.outputs
                if output.case_key == case_key(0) and output.system_id == 'large'
            )
            evaluation = evaluation_with(
                release, (*release.outputs, sample(original, 1), sample(original, 2))
            )
            points = point_snapshot(
                evaluation.evaluation.id,
                evaluation.evaluation.policy_id,
                release.points.points,
            )
            rng = random.Random(0)
            selected: Counter[str] = Counter()
            pool = CandidatePool.build(evaluation, (), (), ())

            for _ in range(1_000):
                picked = pool.draw(
                    points, (), evaluation.policy.method.points_per_logit, rng
                )
                assert picked is not None
                selected[picked.case_key] += 1

            assert pool.remaining == 2
            assert 400 <= selected[case_key(0)] <= 600
            assert selected[case_key(1)] == 1_000 - selected[case_key(0)]

        @pytest.mark.parametrize(
            ('claim_id', 'version'), [(None, None), ('repeat-claim', 4)]
        )
        def test_preserves_the_exact_due_repeat_and_existing_claim_version(
            self, claim_id: str | None, version: int | None
        ) -> None:
            release = make_release(cases=2)
            repeat = DueRepeat(
                receipt_seq=77,
                case_key=case_key(0),
                a_output=output_key(0, 'small'),
                b_output=output_key(0, 'large'),
                claim_id=claim_id,
                claim_version=version,
            )

            pool = CandidatePool.build(release, (), (), (repeat,))

            picked = pool.draw(
                release.points,
                (),
                release.policy.method.points_per_logit,
                random.Random(31),
            )

            assert pool.remaining == 2
            assert picked is not None
            assert picked.repeat_of == 77
            assert picked.case_key == case_key(0)
            assert picked.a_output == repeat.a_output
            assert picked.b_output == repeat.b_output
            assert picked.claim_id == claim_id
            assert picked.claim_version == (version or 0)

        def test_does_not_repeat_a_case_already_used_by_the_session(self) -> None:
            release = make_release(cases=2)
            repeat = DueRepeat(
                receipt_seq=77,
                case_key=case_key(0),
                a_output=output_key(0, 'small'),
                b_output=output_key(0, 'large'),
                claim_id=None,
                claim_version=None,
            )

            pool = CandidatePool.build(release, (), {case_key(0)}, (repeat,))

            picked = pool.draw(
                release.points,
                (),
                release.policy.method.points_per_logit,
                random.Random(31),
            )

            assert pool.remaining == 1
            assert picked is not None
            assert picked.case_key == case_key(1)
            assert picked.repeat_of is None

        @pytest.mark.parametrize(
            ('overlap', 'cases', 'remaining', 'repeat_of'),
            [(False, 1, 1, 77), (True, 1, 1, None), (False, 2, 2, None)],
            ids=['repeat-only', 'overlapping-work', 'distinct-work'],
        )
        def test_progress_and_draw_share_repeat_cases_without_counting_them_twice(
            self, overlap: bool, cases: int, remaining: int, repeat_of: int | None
        ) -> None:
            release = make_release(cases=cases)
            repeat = DueRepeat(
                receipt_seq=77,
                case_key=case_key(0),
                a_output=output_key(0, 'small'),
                b_output=output_key(0, 'large'),
                claim_id=None,
                claim_version=None,
            )
            claims = (
                ()
                if overlap
                else (
                    ClaimState(
                        id='ordinary-claim',
                        kind='ordinary',
                        comparison_key=comparison_identity(
                            case_key(0), 'large', 'small'
                        ),
                        repeat_of=None,
                        version=1,
                        selected_receipt=77,
                        current_lease=None,
                        eligible=True,
                    ),
                )
            )

            pool = CandidatePool.build(release, claims, (), (repeat,))

            picked = pool.draw(
                release.points,
                (),
                release.policy.method.points_per_logit,
                random.Random(0),
            )

            assert pool.remaining == remaining
            assert picked is not None
            assert picked.repeat_of == repeat_of
            assert picked.case_key == case_key(cases - 1)


class TestPairLogWeights:
    @pytest.mark.parametrize(
        ('left', 'right', 'expected'),
        [
            (
                Point(score=0.0, component='one'),
                Point(score=0.0, component='one'),
                0.25,
            ),
            (
                Point(score=100.0 * math.log(3), component='one'),
                Point(score=0.0, component='one'),
                0.1875,
            ),
            (
                Point(score=1_000.0, component='one'),
                Point(score=-1_000.0, component='two'),
                0.25,
            ),
            (
                Point(score=None, component=None),
                Point(score=1_000.0, component='one'),
                0.25,
            ),
        ],
        ids=['equal', 'three-to-one', 'disconnected', 'unrated'],
    )
    def test_combines_comparable_score_information_with_both_orientation_counts(
        self, left: Point, right: Point, expected: float
    ) -> None:
        release = make_release(cases=1, systems=('large', 'small', 'peer'))
        points = point_snapshot(
            release.evaluation.id,
            release.evaluation.policy_id,
            {'large': left, 'small': right, 'peer': left},
        )
        counts = {('large', 'small'): 2, ('small', 'large'): 6}

        forward, baseline = pair_log_weights(
            points, [('large', 'small'), ('large', 'peer')], counts, 100.0
        )
        backward, _ = pair_log_weights(
            points, [('small', 'large'), ('peer', 'large')], counts, 100.0
        )

        assert math.exp(forward) == pytest.approx(expected / 9)
        assert math.exp(baseline) == pytest.approx(0.25)
        assert backward == pytest.approx(forward)

    @pytest.mark.parametrize(
        'scale', [1.0, 100.0, math.ulp(0.0)], ids=['one', 'hundred', 'smallest']
    )
    def test_preserves_tied_minimum_gaps_and_raw_coverage_at_extreme_scales(
        self, scale: float
    ) -> None:
        release = make_release(cases=1, systems=('large', 'medium', 'small'))
        points = point_snapshot(
            release.evaluation.id,
            release.evaluation.policy_id,
            {
                name: Point(score=score, component='connected')
                for name, score in (
                    ('large', -1e308),
                    ('medium', 0.0),
                    ('small', 1e308),
                )
            },
        )
        pairs = [('large', 'medium'), ('medium', 'small'), ('large', 'small')]
        counts = {
            ('large', 'medium'): 2,
            ('medium', 'large'): 2,
            ('medium', 'small'): 9,
            ('small', 'medium'): 9,
            ('large', 'small'): 2,
            ('small', 'large'): 2,
        }

        weights = [
            math.exp(value) for value in pair_log_weights(points, pairs, counts, scale)
        ]

        assert weights == pytest.approx([1 / 5, 1 / 19, 0.0])
