"""Task-clustered pass rates, their 95% intervals, pass^k, and leader separation."""

import statistics
from collections.abc import Iterable, Mapping

from scipy import stats


CONFIDENCE = 0.95


def clopper_pearson(rate: float, tasks: int) -> tuple[float, float]:
    """Return the Clopper-Pearson interval for a mean of `tasks` shares in [0, 1].

    The pass count `rate * tasks` can be fractional with repeated trials.
    No share's variance exceeds the binomial variance at its mean.
    """
    alpha = 1 - CONFIDENCE
    passes = rate * tasks
    lower = stats.beta.ppf(alpha / 2, passes, tasks - passes + 1) if passes else 0.0
    upper = (
        stats.beta.ppf(1 - alpha / 2, passes + 1, tasks - passes)
        if passes < tasks
        else 1.0
    )
    return float(lower), float(upper)


def pass_hat_k(shares: Iterable[float]) -> float:
    """Return the share of tasks whose trials all passed, given each task's share."""
    return statistics.fmean(share == 1 for share in shares)


def separates(leader: Mapping[str, float], other: Mapping[str, float]) -> bool:
    """Whether an exact two-sided sign test favors `leader` at the 5% level.

    The mappings give each task's pass share. The test pairs their shared tasks,
    and tasks where they agree carry no sign. The level is `1 - CONFIDENCE`.
    """
    shared = leader.keys() & other.keys()
    wins = sum(leader[task] > other[task] for task in shared)
    losses = sum(leader[task] < other[task] for task in shared)
    if wins <= losses:
        return False

    return bool(stats.binomtest(wins, wins + losses).pvalue < 1 - CONFIDENCE)
