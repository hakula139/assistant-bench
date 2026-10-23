"""System harness names, each mapped to the Harbor agent that runs it.

Harbor records the agent of every trial in the job's `config.json`, where
`harbor job resume` reads it back, so an import path never changes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from assistant_bench.catalog import System
from assistant_bench.families import Settings
from assistant_bench.harnesses import options


@dataclass(frozen=True)
class Harness:
    """A Harbor agent and how the bench configures it.

    `options` builds the agent's kwargs from a system and the family's settings,
    and the job sends the system's route after `model_prefix` as the model name.
    """

    import_path: str
    options: Callable[[System, Settings], dict[str, Any]]
    model_prefix: str = ''


HARNESSES: dict[str, Harness] = {
    'generate': Harness(
        import_path='assistant_bench.harnesses.generate:Generate',
        options=options.generate,
    ),
    'terminus-2': Harness(
        import_path='assistant_bench.harnesses.terminus:Terminus',
        options=options.terminus,
        # Pins LiteLLM to its OpenAI-compatible provider.
        model_prefix='openai/',
    ),
}
