"""System harness names, each mapped to the Harbor agent that runs it.

A harness maps to its agent's import path, the kwargs it builds from a system and
the family's settings, and the model name the job sends. Harbor records the
agent of every trial in the job's `config.json`, where `harbor job resume` reads
it back, so an import path never changes.
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
    and `model_name` builds the name the job sends.
    """

    import_path: str
    options: Callable[[System, Settings], dict[str, Any]]
    model_name: Callable[[System], str]


HARNESSES: dict[str, Harness] = {
    'generate': Harness(
        import_path='assistant_bench.harnesses.generate:Generate',
        options=options.generate,
        model_name=lambda system: system.route,
    ),
    'terminus-2': Harness(
        import_path='assistant_bench.harnesses.terminus:Terminus',
        options=options.terminus,
        model_name=lambda system: f'openai/{system.route}',
    ),
}
