import pytest

from assistant_bench.arena.resolution import settled, settled_score
from assistant_bench.families import Gate
from assistant_bench.outputs import Output
from assistant_bench.scoring.ranking import Comparison
from support import make_output


def gated(trial: str, system: str, text: str, *failures: Gate) -> Output:
    return make_output(
        trial=trial, case='c', system=system, text=text, gate_failures=list(failures)
    )


class TestSettledScore:
    @pytest.mark.parametrize(
        ('a', 'b', 'score'),
        [
            (gated('1', 'x', 'A', Gate.EMPTY), gated('2', 'y', 'B'), 0.0),
            (gated('1', 'x', 'A'), gated('2', 'y', 'B', Gate.LENGTH), 1.0),
            (
                gated('1', 'x', 'A', Gate.SCRIPT),
                gated('2', 'y', 'B', Gate.TRUNCATED),
                0.5,
            ),
            (gated('1', 'x', 'Same.'), gated('2', 'y', 'Same.'), 0.5),
            (gated('1', 'x', 'A'), gated('2', 'y', 'B'), None),
        ],
        ids=['a-fails', 'b-fails', 'both-fail', 'identical', 'needs-a-vote'],
    )
    def test_decides_only_what_the_outputs_settle(
        self, a: Output, b: Output, score: float | None
    ) -> None:
        assert settled_score(a, b) == score


class TestSettled:
    def test_weighs_each_case_and_pair_as_one_vote_split_across_sides(self) -> None:
        outputs = [
            gated('x1', 'x', 'A', Gate.EMPTY),
            gated('x2', 'x', 'B'),
            gated('y1', 'y', 'C'),
            gated('y2', 'y', 'D'),
            make_output(trial='z1', case='c', system='z', error='TimeoutError'),
        ]

        comparisons = settled(outputs)

        assert comparisons == [
            Comparison('x', 'y', 0.0, 'c', 0.125),
            Comparison('y', 'x', 1.0, 'c', 0.125),
            Comparison('x', 'y', 0.0, 'c', 0.125),
            Comparison('y', 'x', 1.0, 'c', 0.125),
        ]
