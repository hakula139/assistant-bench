"""Actor influence weights for a complete eligible family evaluation."""

from collections import Counter
from collections.abc import Sequence


def actor_weights(actors: Sequence[str], cap: int) -> tuple[float, ...]:
    """Weight aligned ordinary choices using an already validated positive cap.

    Pass the complete eligible family evaluation before taking any subset.
    Repeats and inactive choices do not belong in `actors`.
    """
    counts = Counter(actors)
    return tuple(min(1.0, cap / counts[actor]) for actor in actors)
