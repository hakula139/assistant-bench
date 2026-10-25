"""Content-free diagnostics at boundaries that contain unexpected failures."""

import logging
from typing import Literal


type FailureStage = Literal[
    'preflight', 'validation', 'http', 'assets', 'render', 'render_lookup', 'management'
]


_LOGGER = logging.getLogger(__name__)


def failure(stage: FailureStage, error: Exception) -> None:
    _LOGGER.error(
        'Voting request failed: stage=%s exception=%s.', stage, type(error).__name__
    )
