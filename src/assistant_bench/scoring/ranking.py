"""Bradley-Terry ratings from votes, with case-clustered bootstrap intervals."""

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
RESAMPLES = 1000
CONFIDENCE = 0.95
SEED = 0


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
    """Votes as parallel arrays of system and case indices.

    `sided` holds 1 for a row that the left-side intercept applies to, else 0.
    """

    systems: tuple[str, ...]
    left: Indices
    right: Indices
    score: Floats
    weight: Floats
    sided: Floats
    cluster: Indices

    @classmethod
    def build(cls, comparisons: Sequence[Comparison]) -> Matchups:
        """Index the systems in name order and the cases in order of appearance."""
        systems = tuple(
            sorted({c.left for c in comparisons} | {c.right for c in comparisons})
        )
        index = {system: i for i, system in enumerate(systems)}
        cases: dict[str, int] = {}
        return cls(
            systems=systems,
            left=np.array([index[c.left] for c in comparisons], dtype=np.intp),
            right=np.array([index[c.right] for c in comparisons], dtype=np.intp),
            score=np.array([c.score for c in comparisons], dtype=np.float64),
            weight=np.array([c.weight for c in comparisons], dtype=np.float64),
            sided=np.array([c.sided for c in comparisons], dtype=np.float64),
            cluster=np.array(
                [cases.setdefault(c.case, len(cases)) for c in comparisons],
                dtype=np.intp,
            ),
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
        _, cluster = np.unique(self.cluster[rows], return_inverse=True)
        return Matchups(
            systems=tuple(
                s for s, keep in zip(self.systems, kept, strict=True) if keep
            ),
            left=index[self.left[rows]],
            right=index[self.right[rows]],
            score=self.score[rows],
            weight=self.weight[rows],
            sided=self.sided[rows],
            cluster=cluster.astype(np.intp),
        )


@dataclass(frozen=True)
class Fit:
    """Strengths in logits with mean zero, plus the left side's advantage."""

    strength: Floats
    position: float
    covariate: float | None


@dataclass(frozen=True)
class Ratings:
    """Scores on the Elo-like scale with their bootstrap intervals.

    `draws` holds each resample's scores, one row per resample, with NaN where
    the resample leaves a system unconnected to the root. Such a draw counts as
    unbounded, so a bound that falls on one is infinite.
    """

    systems: tuple[str, ...]
    score: Floats
    lower: Floats
    upper: Floats
    draws: Floats
    position: float

    def anchored(self, system: str) -> Ratings:
        """Move the scale so `system` sits at `ANCHOR`, shifting every score alike."""
        shift = ANCHOR - self.score[self.systems.index(system)]
        return replace(
            self,
            score=self.score + shift,
            lower=self.lower + shift,
            upper=self.upper + shift,
            draws=self.draws + shift,
        )

    def bounded(self) -> Ratings:
        """Keep the systems whose interval is finite."""
        kept = np.isfinite(self.lower) & np.isfinite(self.upper)
        return Ratings(
            systems=tuple(
                s for s, keep in zip(self.systems, kept, strict=True) if keep
            ),
            score=self.score[kept],
            lower=self.lower[kept],
            upper=self.upper[kept],
            draws=self.draws[:, kept],
            position=self.position,
        )


def fit(matchups: Matchups, covariate: Floats | None = None) -> Fit:
    """Fit Bradley-Terry with a left-side intercept by penalized logistic loss.

    `covariate` adds a per-vote feature, such as a length difference.
    """
    columns = [] if covariate is None else [covariate]
    x = _design(
        matchups.left, matchups.right, matchups.sided, len(matchups.systems), columns
    )
    # With a thread per core, scipy's OpenBLAS burns several cores during each tiny
    # L-BFGS-B fit, which on a loaded host slows the fit itself.
    with threadpool_limits(limits=1, user_api='blas'):
        theta = _minimize(x, matchups.score, matchups.weight, len(matchups.systems))
    return _fit(theta, len(matchups.systems))


def bootstrap(matchups: Matchups, root: str, resamples: int = RESAMPLES) -> Floats:
    """Refit on cases drawn with replacement, returning one row of strengths each.

    Votes on one case are correlated, so each draw takes all of a case's votes.
    The votes of each ordered pair and sidedness collapse into one weighted row,
    which keeps a refit cheap. A system that a resample's drawn rows leave
    unconnected to `root` gets NaN there, since the penalty would place it at its
    own component's center.
    """
    k = len(matchups.systems)
    root_index = matchups.systems.index(root)
    keys = np.stack([matchups.left, matchups.right, matchups.sided], axis=1)
    pairs, pair_of_vote = np.unique(keys, axis=0, return_inverse=True)
    x = _design(
        pairs[:, 0].astype(np.intp), pairs[:, 1].astype(np.intp), pairs[:, 2], k, []
    )
    membership = np.zeros((len(matchups.score), len(pairs)))
    membership[np.arange(len(matchups.score)), pair_of_vote] = 1

    rng = np.random.default_rng(SEED)
    n_cases = int(matchups.cluster.max()) + 1
    draws = rng.multinomial(n_cases, np.full(n_cases, 1 / n_cases), size=resamples)
    vote_weights = draws[:, matchups.cluster] * matchups.weight
    left, right = pairs[:, 0].astype(np.intp), pairs[:, 1].astype(np.intp)
    strengths = np.empty((resamples, k))
    with threadpool_limits(limits=1, user_api='blas'):
        pair_weights = vote_weights @ membership
        pair_wins = (vote_weights * matchups.score) @ membership
        start = _minimize(
            x,
            pair_wins.sum(axis=0) / pair_weights.sum(axis=0),
            pair_weights.sum(axis=0),
            k,
        )
        for r in range(resamples):
            weights = pair_weights[r]
            drawn = weights > 0
            score = np.divide(
                pair_wins[r], weights, out=np.full(len(pairs), 0.5), where=drawn
            )
            strengths[r] = _fit(_minimize(x, score, weights, k, start), k).strength
            connected = _connected(left[drawn], right[drawn], k, root_index)
            strengths[r, ~connected] = np.nan
    return strengths


def rate(matchups: Matchups, root: str, resamples: int = RESAMPLES) -> Ratings:
    """Place the root system's score at `ANCHOR` with percentile intervals.

    Each resample's scores shift so that the systems it connects keep the mean of
    their point scores, which gives the root an interval like every other system
    and keeps a detached system's displacement out of the others' draws.
    """
    point = fit(matchups)
    shift = point.strength[matchups.systems.index(root)]
    score = ANCHOR + ELO_PER_LOGIT * (point.strength - shift)
    draws = ANCHOR + ELO_PER_LOGIT * (bootstrap(matchups, root, resamples) - shift)
    draws += np.nanmean(score - draws, axis=1, keepdims=True)
    lower, upper = _interval(draws)
    return Ratings(
        systems=matchups.systems,
        score=score,
        lower=lower,
        upper=upper,
        draws=draws,
        position=ELO_PER_LOGIT * point.position,
    )


def ties(ratings: Ratings, candidates: Sequence[int]) -> list[int]:
    """Return the candidates tied with the top-scoring one, itself included.

    A candidate ties when the interval of its score's difference from the top's,
    paired within each resample, contains 0, as an unbounded interval does.
    """
    top = max(candidates, key=lambda i: ratings.score[i])
    lower, upper = _interval(ratings.draws[:, candidates] - ratings.draws[:, [top]])
    return [
        i
        for i, low, high in zip(candidates, lower, upper, strict=True)
        if low <= 0 <= high
    ]


def rank_spread(lower: Floats, upper: Floats) -> tuple[Indices, Indices]:
    """Return each system's best and worst rank.

    The best rank counts the systems whose lower bound exceeds this system's upper
    bound, and the worst rank counts the others whose upper bound exceeds this
    system's lower bound.
    """
    others = ~np.eye(len(lower), dtype=bool)
    best = 1 + (lower[None, :] > upper[:, None]).sum(axis=1)
    worst = 1 + ((upper[None, :] > lower[:, None]) & others).sum(axis=1)
    return best, worst


def separability(lower: Floats, upper: Floats) -> float | None:
    """Return the share of system pairs whose intervals do not overlap."""
    i, j = np.triu_indices(len(lower), k=1)
    if not len(i):
        return None
    apart = (upper[i] < lower[j]) | (upper[j] < lower[i])
    return float(apart.mean())


def rank_agreement(first: Floats, second: Floats) -> float | None:
    """Return Kendall's τ between two score vectors, undefined below two systems."""
    if len(first) < 2:
        return None
    statistic = float(stats.kendalltau(first, second).statistic)
    return None if np.isnan(statistic) else statistic


def _interval(draws: Floats) -> tuple[Floats, Floats]:
    """Return the percentile bounds, rounded outward to the nearest resample.

    Rounding to a resample keeps an unbounded draw from entering interpolation,
    so a bound that reaches one is infinite.
    """
    tail = (1 - CONFIDENCE) / 2 * 100
    lower = np.percentile(
        np.nan_to_num(draws, nan=-np.inf), tail, axis=0, method='lower'
    )
    upper = np.percentile(
        np.nan_to_num(draws, nan=np.inf), 100 - tail, axis=0, method='higher'
    )
    return lower, upper


def _connected(
    left: Indices, right: Indices, k: int, system: int
) -> npt.NDArray[np.bool_]:
    """Mark the systems that the rows connect to `system`."""
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


def _minimize(
    x: Floats, score: Floats, weights: Floats, k: int, start: Floats | None = None
) -> Floats:
    penalty = np.zeros(x.shape[1])
    penalty[:k] = L2_PENALTY

    def loss(theta: Floats) -> tuple[float, Floats]:
        z = x @ theta
        nll = weights @ (score * np.logaddexp(0, -z) + (1 - score) * np.logaddexp(0, z))
        gradient = x.T @ (weights * (special.expit(z) - score))
        return float(nll + penalty @ theta**2 / 2), gradient + penalty * theta

    result = optimize.minimize(
        loss,
        np.zeros(x.shape[1]) if start is None else start,
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
