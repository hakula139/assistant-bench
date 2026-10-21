"""Bradley-Terry ratings from votes, with case-clustered bootstrap intervals."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import numpy.typing as npt
from scipy import optimize, special, stats


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
    """One vote, with `score` the left side's result: 1, 0, or 0.5 for a tie."""

    left: str
    right: str
    score: float
    case: str


@dataclass(frozen=True)
class Matchups:
    """Votes as parallel arrays of system and case indices."""

    systems: tuple[str, ...]
    left: Indices
    right: Indices
    score: Floats
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
            cluster=np.array(
                [cases.setdefault(c.case, len(cases)) for c in comparisons],
                dtype=np.intp,
            ),
        )


@dataclass(frozen=True)
class Fit:
    """Strengths in logits with mean zero, plus the left side's advantage."""

    strength: Floats
    position: float
    covariate: float | None


@dataclass(frozen=True)
class Ratings:
    """Scores on the Elo-like scale with their bootstrap intervals."""

    systems: tuple[str, ...]
    score: Floats
    lower: Floats
    upper: Floats
    position: float


def fit(matchups: Matchups, covariate: Floats | None = None) -> Fit:
    """Fit Bradley-Terry with a left-side intercept by penalized logistic loss.

    `covariate` adds a per-vote feature, such as a length difference.
    """
    columns = [] if covariate is None else [covariate]
    x = _design(matchups.left, matchups.right, len(matchups.systems), columns)
    weights = np.ones_like(matchups.score)
    theta = _minimize(x, matchups.score, weights, len(matchups.systems))
    return _fit(theta, len(matchups.systems))


def bootstrap(matchups: Matchups, resamples: int = RESAMPLES) -> Floats:
    """Refit on cases drawn with replacement, returning one row of strengths each.

    Votes on one case are correlated, so each draw takes all of a case's votes.
    The votes of each ordered pair collapse into one weighted row, which keeps a
    refit cheap.
    """
    k = len(matchups.systems)
    sides = np.stack([matchups.left, matchups.right], axis=1)
    pairs, pair_of_vote = np.unique(sides, axis=0, return_inverse=True)
    x = _design(pairs[:, 0], pairs[:, 1], k, [])
    membership = np.zeros((len(matchups.score), len(pairs)))
    membership[np.arange(len(matchups.score)), pair_of_vote] = 1

    rng = np.random.default_rng(SEED)
    n_cases = int(matchups.cluster.max()) + 1
    draws = rng.multinomial(n_cases, np.full(n_cases, 1 / n_cases), size=resamples)
    vote_weights = draws[:, matchups.cluster].astype(np.float64)
    pair_weights = vote_weights @ membership
    pair_wins = (vote_weights * matchups.score) @ membership

    start = _minimize(
        x, pair_wins.sum(axis=0) / pair_weights.sum(axis=0), pair_weights.sum(axis=0), k
    )
    strengths = np.empty((resamples, k))
    for r in range(resamples):
        weights = pair_weights[r]
        score = np.divide(
            pair_wins[r], weights, out=np.full(len(pairs), 0.5), where=weights > 0
        )
        strengths[r] = _fit(_minimize(x, score, weights, k, start), k).strength
    return strengths


def rate(matchups: Matchups, reference: str, resamples: int = RESAMPLES) -> Ratings:
    """Place the reference system's score at `ANCHOR` with percentile intervals.

    Each resample's strengths keep mean zero and take the point fit's offset, so
    the reference has an interval like every other system.
    """
    point = fit(matchups)
    offset = ANCHOR - ELO_PER_LOGIT * point.strength[matchups.systems.index(reference)]
    draws = offset + ELO_PER_LOGIT * bootstrap(matchups, resamples)
    tail = (1 - CONFIDENCE) / 2 * 100
    return Ratings(
        systems=matchups.systems,
        score=offset + ELO_PER_LOGIT * point.strength,
        lower=np.percentile(draws, tail, axis=0),
        upper=np.percentile(draws, 100 - tail, axis=0),
        position=ELO_PER_LOGIT * point.position,
    )


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


def _design(left: Indices, right: Indices, k: int, columns: list[Floats]) -> Floats:
    rows = np.arange(len(left))
    x = np.zeros((len(left), k + 1 + len(columns)))
    x[rows, left] = 1
    x[rows, right] = -1
    x[:, k] = 1
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
