"""Penalized Bradley-Terry point ratings and their descriptive diagnostics."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import numpy.typing as npt
from scipy import optimize, sparse, special, stats
from threadpoolctl import threadpool_limits

from assistant_bench.families import Choice


type Floats = npt.NDArray[np.float64]
type Indices = npt.NDArray[np.intp]

ELO_PER_LOGIT = 400 / math.log(10)
ANCHOR = 1000.0
# Keeps the strength of a system that wins every vote finite.
L2_PENALTY = 0.01
INTERVAL_LEVEL = 0.95
# The linear program's optimum above which a round counts as separated.
SEPARATION_TOLERANCE = 1e-7
LEFT_SCORE: dict[Choice, float] = {'a': 1.0, 'b': 0.0, 'tie': 0.5, 'both_bad': 0.5}


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
    """Comparisons as parallel arrays of system and case indices.

    `sided` holds 1 for a row that the left-side intercept applies to, else 0.
    """

    systems: tuple[str, ...]
    left: Indices
    right: Indices
    case: Indices
    score: Floats
    weight: Floats
    sided: Floats

    @classmethod
    def build(cls, comparisons: Sequence[Comparison]) -> Matchups:
        """Index the systems and the cases in name order."""
        systems = tuple(
            sorted({c.left for c in comparisons} | {c.right for c in comparisons})
        )
        index = {system: i for i, system in enumerate(systems)}
        cases = {
            case: i for i, case in enumerate(sorted({c.case for c in comparisons}))
        }

        return cls(
            systems=systems,
            left=np.array([index[c.left] for c in comparisons], dtype=np.intp),
            right=np.array([index[c.right] for c in comparisons], dtype=np.intp),
            case=np.array([cases[c.case] for c in comparisons], dtype=np.intp),
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
            case=self.case[rows],
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
    """Fitted scores on the Elo-like scale."""

    systems: tuple[str, ...]
    score: Floats


@dataclass(frozen=True)
class Uncertainty:
    """A component's case-clustered covariance and its t(G − 1) quantile.

    `covariance` holds the centered strengths in squared logits, and `critical` the
    two-sided `INTERVAL_LEVEL` quantile over the component's G cases.
    """

    covariance: Floats
    critical: float

    def half_widths(self) -> Floats:
        """Return each system's half-width in score points, the anchor's included."""
        widths: Floats = (
            self.critical * ELO_PER_LOGIT * np.sqrt(np.diag(self.covariance))
        )
        return widths

    def separates(self, ratings: PointRatings, leader: str, other: str) -> bool:
        """Whether a two-sided Wald test of the two systems' gap rejects a tie."""
        i, j = (ratings.systems.index(system) for system in (leader, other))
        gap = (ratings.score[i] - ratings.score[j]) / ELO_PER_LOGIT
        c = self.covariance
        return bool(abs(gap) > self.critical * np.sqrt(c[i, i] + c[j, j] - 2 * c[i, j]))


@dataclass(frozen=True)
class Component:
    """The point fit of one connected component."""

    root: str
    """System incident on the component's greatest effective human weight."""
    anchor: str
    """System at `ANCHOR`, the reference when the component holds it, else the root."""
    ratings: PointRatings
    cases: int
    """Cases with a weighted comparison, the clusters behind `uncertainty`."""
    uncertainty: Uncertainty | None
    """Null below two cases, where no clustered variance exists, or when the
    comparisons are separated, where the variance collapses toward zero."""


@dataclass(frozen=True)
class SplitDifference:
    systems: tuple[str, ...]
    difference: Floats
    half_width: Floats


def split_difference(
    public: Sequence[Comparison], held_out: Sequence[Comparison]
) -> tuple[SplitDifference | None, str | None]:
    """Compare centered strengths on one common field with independent case clusters."""
    if any(
        len({row.case for row in rows if row.weight > 0}) < 30
        for rows in (public, held_out)
    ):
        return None, 'insufficient_cases'
    fields = [
        {system for row in rows if row.weight > 0 for system in (row.left, row.right)}
        for rows in (public, held_out)
    ]
    common = fields[0] & fields[1]
    if len(common) < 2:
        return None, 'no_common_field'
    matchups = [
        Matchups.build(
            [row for row in rows if row.weight > 0 and {row.left, row.right} <= common]
        )
        for rows in (public, held_out)
    ]
    if any(set(rows.systems) != common for rows in matchups):
        return None, 'disconnected_field'
    if any(
        len(rows.component(rows.systems[0]).systems) != len(common) for rows in matchups
    ):
        return None, 'disconnected_field'
    if any(_weighted_cases(rows) < 30 for rows in matchups):
        return None, 'insufficient_cases'
    for rows in matchups:
        design = _design(rows.left, rows.right, rows.sided, len(common), [])
        required_rank = len(common) - 1 + int(bool(np.any(rows.sided)))
        if np.linalg.matrix_rank(design) < required_rank:
            return None, 'unidentifiable_field'
        if _separated(rows):
            return None, 'separated_fit'
    points = [fit(rows) for rows in matchups]
    covariance = sum(
        (_covariance(rows, point) for rows, point in zip(matchups, points, strict=True))
    )
    critical = float(
        stats.t.ppf(
            (1 + INTERVAL_LEVEL) / 2,
            min(_weighted_cases(rows) for rows in matchups) - 1,
        )
    )
    return SplitDifference(
        matchups[0].systems,
        ELO_PER_LOGIT * (points[0].strength - points[1].strength),
        critical * ELO_PER_LOGIT * np.sqrt(np.diag(covariance)),
    ), None


def fit(matchups: Matchups, covariate: Floats | None = None) -> Fit:
    """Fit Bradley-Terry with a left-side intercept by penalized logistic loss.

    `covariate` adds a per-vote feature, which the fit drops unless it is
    identifiable apart from the system and position effects.
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


def rate_point(matchups: Matchups, anchor: str) -> PointRatings:
    """Place the anchor system's point score at `ANCHOR`."""
    return _point_ratings(matchups.systems, fit(matchups), anchor)


def rate_root(
    comparisons: Sequence[Comparison], order: Sequence[str], reference: str
) -> Component:
    """Fit the component of the root, given at least one comparison.

    The root is the system incident on the greatest effective human weight, and
    `order` breaks ties. The reference anchors the component when it belongs to it,
    and the root anchors it otherwise.
    """
    matchups = Matchups.build(comparisons)
    root = matchups.root(order)
    component = matchups.component(root)
    anchor = reference if reference in component.systems else root
    point = fit(component)
    cases = _weighted_cases(component)
    return Component(
        root,
        anchor,
        _point_ratings(component.systems, point, anchor),
        cases,
        None
        if cases < 2 or _separated(component)
        else Uncertainty(
            _covariance(component, point),
            float(stats.t.ppf((1 + INTERVAL_LEVEL) / 2, cases - 1)),
        ),
    )


def _separated(matchups: Matchups) -> bool:
    """Whether some strengths and left-side advantage order every decided row.

    Such a direction fits every win and loss at least as well as a tie, with the
    half scores tied, and strictly fits some. Without the penalty the fit would
    then diverge, so the sandwich collapses toward zero.
    """
    weighted = matchups.weight > 0
    x = _design(
        matchups.left, matchups.right, matchups.sided, len(matchups.systems), []
    )[weighted]
    score = matchups.score[weighted]
    sign = np.sign(score - 0.5)
    decided = x[sign != 0] * sign[sign != 0, None]
    tied = x[sign == 0]
    result = optimize.linprog(
        -decided.sum(axis=0),
        A_ub=-decided,
        b_ub=np.zeros(len(decided)),
        A_eq=tied if len(tied) else None,
        b_eq=np.zeros(len(tied)) if len(tied) else None,
        bounds=(-1, 1),
    )
    if result.fun is None:
        raise RuntimeError(f'the separation check did not solve: {result.message}')
    return bool(-result.fun > SEPARATION_TOLERANCE)


def _covariance(matchups: Matchups, point: Fit) -> Floats:
    """Return the CR1 sandwich covariance of the strengths, by case.

    The score sums of each case form the meat, scaled by G / (G − 1) over the G
    cases with a weighted comparison, so votes on one case count as one draw.
    Each design row sums to zero over the strengths, and the penalty is equal
    across them, so the covariance already lies on the centered strengths.
    """
    k = len(matchups.systems)
    x = _design(matchups.left, matchups.right, matchups.sided, k, [])
    p = special.expit(x @ np.append(point.strength, point.position))
    penalty = np.zeros(k + 1)
    penalty[:k] = L2_PENALTY

    hessian = x.T @ (x * (matchups.weight * p * (1 - p))[:, None]) + np.diag(penalty)
    _, cluster = np.unique(matchups.case, return_inverse=True)
    sums = np.zeros((cluster.max() + 1, k + 1))
    np.add.at(sums, cluster, x * (matchups.weight * (p - matchups.score))[:, None])
    clusters = _weighted_cases(matchups)

    # The left-side column is empty when no comparison is sided, which leaves the
    # Hessian singular in that direction alone.
    bread = np.linalg.pinv(hessian)
    meat = sums @ bread[:, :k] * math.sqrt(clusters / (clusters - 1))
    covariance: Floats = meat.T @ meat
    return covariance


def length_feature(
    lengths: Sequence[tuple[int, int]], weights: Sequence[float]
) -> Floats | None:
    """Return the weighted z-score of each vote's (A − B) / (A + B) output lengths.

    Returns None when every vote has the same relative length difference.
    """
    a = np.array([left for left, _ in lengths])
    b = np.array([right for _, right in lengths])
    total = a + b
    relative = np.divide(a - b, total, out=np.zeros(len(lengths)), where=total > 0)
    if not lengths or np.ptp(relative) == 0:
        return None

    centered = relative - np.average(relative, weights=weights)
    feature: Floats = centered / np.sqrt(np.average(centered**2, weights=weights))
    return feature


def rank_agreement(first: Floats, second: Floats) -> float | None:
    """Return Kendall's τ between two score vectors, undefined below two systems."""
    if len(first) < 2:
        return None

    statistic = float(stats.kendalltau(first, second).statistic)
    return None if np.isnan(statistic) else statistic


def split_half_tau(votes: Sequence[Comparison], root: str) -> float | None:
    """Compare the point orders fit to alternating votes, if each half has the root.

    The orders cover the systems both halves connect to the root. Alternating
    votes form the halves, since a round may have only two or three voters.
    """
    halves = [votes[half::2] for half in (0, 1)]
    if not all(any(root in (c.left, c.right) for c in half) for half in halves):
        return None

    first, second = (
        dict(zip(fitted.systems, fit(fitted).strength, strict=True))
        for fitted in (Matchups.build(half).component(root) for half in halves)
    )
    shared = sorted(first.keys() & second.keys())
    return rank_agreement(
        np.array([first[s] for s in shared]), np.array([second[s] for s in shared])
    )


def _weighted_cases(matchups: Matchups) -> int:
    return len(np.unique(matchups.case[matchups.weight > 0]))


def _point_ratings(systems: tuple[str, ...], point: Fit, anchor: str) -> PointRatings:
    shift = point.strength[systems.index(anchor)]
    return PointRatings(
        systems=systems, score=ANCHOR + ELO_PER_LOGIT * (point.strength - shift)
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
