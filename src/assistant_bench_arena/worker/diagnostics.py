"""Content-free diagnostics at boundaries that contain unexpected failures."""

import logging
from typing import Literal

from assistant_bench_arena.transport import OwnerInputError


type FailureStage = Literal[
    'preflight', 'validation', 'http', 'assets', 'render', 'render_lookup', 'management'
]


_LOGGER = logging.getLogger(__name__)


def failure(stage: FailureStage, error: Exception) -> None:
    """Record the boundary that failed without any value the request supplied."""
    _LOGGER.error(
        'Voting request failed: stage=%s exception=%s.', stage, type(error).__name__
    )


def rejected(error: OwnerInputError) -> None:
    """Record the owner's private refusal, whose message is authored diagnosis."""
    _LOGGER.error(
        'Voting management rejected: exception=%s message=%s',
        type(error).__name__,
        error,
    )
