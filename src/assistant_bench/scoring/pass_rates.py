"""Task-clustered pass rates, their 95% intervals, and paired leader separation."""

from collections.abc import Sequence

from scipy import stats


CONFIDENCE = 0.95


def clopper_pearson(rate: float, tasks: int) -> tuple[float, float]:
    """Return the Clopper-Pearson interval for a mean of `tasks` shares in [0, 1].

    `rate * tasks` serves as the pass count, fractional when tasks average
    several trials. No share's variance exceeds the binomial variance at its
    mean, and simulated task-clustered rates keep at least 95% coverage.
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


def separates(leader: Sequence[float], other: Sequence[float]) -> bool:
    """Whether an exact two-sided sign test favors `leader` at the 5% level.

    The sequences pair each shared task's pass share, and tasks where they agree
    carry no sign. The level is `1 - CONFIDENCE`.
    """
    wins = sum(a > b for a, b in zip(leader, other, strict=True))
    losses = sum(a < b for a, b in zip(leader, other, strict=True))
    if wins <= losses:
        return False

    return bool(stats.binomtest(wins, wins + losses).pvalue < 1 - CONFIDENCE)
