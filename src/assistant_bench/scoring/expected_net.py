"""Expected net points against the family's fixed reference."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.special import expit
from threadpoolctl import threadpool_limits

from assistant_bench.scoring import ranking


@dataclass(frozen=True)
class Scores:
    points: dict[str, float]
    cases: int


def rate(comparisons: Sequence[ranking.Comparison], reference: str) -> Scores:
    """Estimate win minus loss minus joint rejection with no pane advantage.

    The preference graph excludes joint rejections. A rejected pair gives no
    relative evidence, but still reduces both systems' expected points.
    """
    preference = [row for row in comparisons if row.weight > 0 and not row.both_bad]
    directional = ranking.Matchups.build(preference)
    if reference not in directional.systems:
        return Scores({}, 0)

    anchored = directional.component(reference)
    systems = anchored.systems
    index = {system: i for i, system in enumerate(systems)}
    connected = [
        row
        for row in comparisons
        if row.weight > 0 and row.left in index and row.right in index
    ]
    position = index[reference]
    with threadpool_limits(limits=1, user_api='blas'):
        strength = ranking.fit(anchored, identify_position=True).strength
        rejection, identified = _joint_rejection(connected, index, position)

    margin: ranking.Floats = 2 * expit(strength - float(strength[position])) - 1
    points = np.clip((1 - rejection) * margin - rejection, -1, 1)
    cases = len({row.case for row in connected})
    return Scores(
        {
            system: float(point)
            for system, point, usable in zip(systems, points, identified, strict=True)
            if usable
        },
        cases,
    )


def _joint_rejection(
    comparisons: Sequence[ranking.Comparison], index: dict[str, int], reference: int
) -> tuple[ranking.Floats, npt.NDArray[np.bool_]]:
    k = len(index)
    outcomes = np.array([row.both_bad for row in comparisons], dtype=np.float64)
    if not outcomes.any():
        return np.zeros(k), np.ones(k, dtype=bool)

    design = np.zeros((len(comparisons), k + 1))
    rows = np.arange(len(comparisons))
    design[rows, [index[row.left] for row in comparisons]] = 1
    design[rows, [index[row.right] for row in comparisons]] = 1
    design[:, k] = 1
    targets = np.column_stack((np.eye(k), np.ones(k)))
    targets[:, reference] += 1

    # Ridge shrinkage selects finite parameters even for an unobserved matchup.
    # Its prediction is identified only when the observed rows span the target.
    edges = np.unique(design, axis=0)
    identified = np.all(
        np.isclose((targets @ np.linalg.pinv(edges)) @ edges, targets), axis=1
    )
    weights = np.array([row.weight for row in comparisons])
    coef = ranking.fit_logistic(design, outcomes, weights, k)
    return expit(targets @ coef), identified
