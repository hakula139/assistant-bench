"""Actor influence weights for a complete eligible family evaluation."""

from collections import Counter
from collections.abc import Sequence


CAP = 100


def actor_weights(actors: Sequence[str]) -> tuple[float, ...]:
    """Weight aligned ordinary choices so each actor's total is at most `CAP`.

    Pass the complete eligible family evaluation before taking any subset.
    Repeats and inactive choices do not belong in `actors`.
    """
    counts = Counter(actors)
    return tuple(min(1.0, CAP / counts[actor]) for actor in actors)
