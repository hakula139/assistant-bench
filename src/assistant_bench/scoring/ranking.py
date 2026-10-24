"""Penalized Bradley-Terry point ratings from weighted comparisons."""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

import numpy as np
import numpy.typing as npt
from scipy import optimize, sparse, special, stats
from threadpoolctl import threadpool_limits


type Floats = npt.NDArray[np.float64]
type Indices = npt.NDArray[np.intp]

ELO_PER_LOGIT = 400 / math.log(10)
ANCHOR = 1000.0
# Keeps the strength of a system that wins every vote finite.
L2_PENALTY = 0.01


class Comparison(NamedTuple):
    """One vote or a share of one, with `score` the left side's result.

    `score` is 1, 0, or 0.5 for a tie, and `weight` the share of a vote it counts.
    `sided` marks a vote, whose pane order the left-side intercept models.
    """

    left: str
    right: str
    score: float
    case: str
    weight: float = 1.0
    sided: bool = True


@dataclass(frozen=True)
class Matchups:
    """Comparisons as parallel arrays of system indices.

    `sided` holds 1 for a row that the left-side intercept applies to, else 0.
    """

    systems: tuple[str, ...]
    left: Indices
    right: Indices
    score: Floats
    weight: Floats
    sided: Floats

    @classmethod
    def build(cls, comparisons: Sequence[Comparison]) -> Matchups:
        """Index the systems in name order."""
        systems = tuple(
            sorted({c.left for c in comparisons} | {c.right for c in comparisons})
        )
        index = {system: i for i, system in enumerate(systems)}

        return cls(
            systems=systems,
            left=np.array([index[c.left] for c in comparisons], dtype=np.intp),
            right=np.array([index[c.right] for c in comparisons], dtype=np.intp),
            score=np.array([c.score for c in comparisons], dtype=np.float64),
            weight=np.array([c.weight for c in comparisons], dtype=np.float64),
            sided=np.array([c.sided for c in comparisons], dtype=np.float64),
        )

    def component(self, system: str) -> Matchups:
        """Keep the systems that comparisons connect to `system`, with their rows.

        Strengths are comparable only within one connected component, since the
        penalty centers each component on its own.
        """
        kept = _connected(
            self.left, self.right, len(self.systems), self.systems.index(system)
        )
        rows = kept[self.left]
        index = np.cumsum(kept) - 1

        return Matchups(
            systems=tuple(
                s for s, keep in zip(self.systems, kept, strict=True) if keep
            ),
            left=index[self.left[rows]],
            right=index[self.right[rows]],
            score=self.score[rows],
            weight=self.weight[rows],
            sided=self.sided[rows],
        )

    def root(self, order: Sequence[str]) -> str:
        """Return the system incident on the greatest effective human weight.

        Only sided rows count as votes. A tie follows `order`, then the name.
        """
        sided = self.sided > 0
        rows = np.concatenate([self.left[sided], self.right[sided]])
        weights = np.concatenate([self.weight[sided], self.weight[sided]])
        counts = np.bincount(rows, weights=weights, minlength=len(self.systems))
        votes = dict(zip(self.systems, counts, strict=True))
        position = {system: i for i, system in enumerate(order)}

        return min(
            self.systems,
            key=lambda system: (
                -votes[system],
                position.get(system, len(position)),
                system,
            ),
        )


@dataclass(frozen=True)
class Fit:
    """Strengths in logits with mean zero, plus the left side's advantage."""

    strength: Floats
    position: float
    covariate: float | None


@dataclass(frozen=True)
class PointRatings:
    """Fitted scores on the Elo-like scale, plus the left side's advantage."""

    systems: tuple[str, ...]
    score: Floats
    position: float

    def anchored(self, system: str) -> PointRatings:
        """Shift every score so `system` sits at `ANCHOR`."""
        shift = ANCHOR - self.score[self.systems.index(system)]
        return replace(self, score=self.score + shift)


def fit(matchups: Matchups, covariate: Floats | None = None) -> Fit:
    """Fit Bradley-Terry with a left-side intercept by penalized logistic loss.

    `covariate` adds a per-vote feature, such as a length difference, when it is
    identifiable apart from system and position effects on positive-weight rows.
    """
    columns: list[Floats] = [] if covariate is None else [covariate]
    x = _design(
        matchups.left, matchups.right, matchups.sided, len(matchups.systems), columns
    )

    # With a thread per core, scipy's OpenBLAS burns several cores during each tiny
    # L-BFGS-B fit, which on a loaded host slows the fit itself.
    with threadpool_limits(limits=1, user_api='blas'):
        if covariate is not None:
            observed = x[matchups.weight > 0]
            if np.linalg.matrix_rank(observed) <= np.linalg.matrix_rank(
                observed[:, :-1]
            ):
                x = x[:, :-1]
        theta = _minimize(x, matchups.score, matchups.weight, len(matchups.systems))

    return _fit(theta, len(matchups.systems))


def rate_point(matchups: Matchups, root: str) -> PointRatings:
    """Place the root system's point score at `ANCHOR`."""
    return _point_ratings(matchups.systems, fit(matchups), root)


def rank_agreement(first: Floats, second: Floats) -> float | None:
    """Return Kendall's τ between two score vectors, undefined below two systems."""
    if len(first) < 2:
        return None

    statistic = float(stats.kendalltau(first, second).statistic)
    return None if np.isnan(statistic) else statistic


def _point_ratings(systems: tuple[str, ...], point: Fit, root: str) -> PointRatings:
    shift = point.strength[systems.index(root)]
    return PointRatings(
        systems=systems,
        score=ANCHOR + ELO_PER_LOGIT * (point.strength - shift),
        position=ELO_PER_LOGIT * point.position,
    )


def _connected(
    left: Indices, right: Indices, k: int, system: int
) -> npt.NDArray[np.bool_]:
    graph = sparse.coo_array((np.ones(len(left)), (left, right)), shape=(k, k))
    _, labels = sparse.csgraph.connected_components(graph, directed=False)
    connected: npt.NDArray[np.bool_] = labels == labels[system]
    return connected


def _design(
    left: Indices, right: Indices, sided: Floats, k: int, columns: list[Floats]
) -> Floats:
    rows = np.arange(len(left))
    x = np.zeros((len(left), k + 1 + len(columns)))
    x[rows, left] = 1
    x[rows, right] = -1
    x[:, k] = sided
    for offset, column in enumerate(columns, start=k + 1):
        x[:, offset] = column

    return x


def _minimize(x: Floats, score: Floats, weights: Floats, k: int) -> Floats:
    penalty = np.zeros(x.shape[1])
    penalty[:k] = L2_PENALTY

    def loss(theta: Floats) -> tuple[float, Floats]:
        z = x @ theta
        nll = weights @ (score * np.logaddexp(0, -z) + (1 - score) * np.logaddexp(0, z))
        gradient = x.T @ (weights * (special.expit(z) - score))
        return float(nll + penalty @ theta**2 / 2), gradient + penalty * theta

    result = optimize.minimize(
        loss,
        np.zeros(x.shape[1]),
        jac=True,
        method='L-BFGS-B',
    )
    if not result.success:
        raise RuntimeError(f'the Bradley-Terry fit did not converge: {result.message}')

    theta: Floats = result.x
    return theta


def _fit(theta: Floats, k: int) -> Fit:
    strength = theta[:k] - theta[:k].mean()
    covariate = float(theta[k + 1]) if len(theta) > k + 1 else None
    return Fit(strength=strength, position=float(theta[k]), covariate=covariate)
