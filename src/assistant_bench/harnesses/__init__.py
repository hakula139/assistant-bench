"""System harness names, each mapped to the Harbor agent that runs it.

Harbor records each trial's agent and environment in the job's `config.json`,
where `harbor job resume` reads them back, so an import path never changes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from assistant_bench.catalog import System
from assistant_bench.harnesses import options


@dataclass(frozen=True)
class Harness:
    """A Harbor agent and how the bench configures it."""

    import_path: str
    options: Callable[[System], dict[str, Any]]
    model_prefix: str = ''
    """Prepended to the system route in the job's model name."""

    sends_prompt: bool = False
    single_call: bool = False
    """Makes one request without acting in the task's environment.

    A job using only these harnesses runs in `HOST_ENVIRONMENT` without a
    container. They cannot solve task-family tasks.
    """


HOST_ENVIRONMENT = 'assistant_bench.harnesses.host:HostEnvironment'

HARNESSES: dict[str, Harness] = {
    'generate': Harness(
        import_path='assistant_bench.harnesses.generate:Generate',
        options=options.generate,
        sends_prompt=True,
        single_call=True,
    ),
    'terminus-2': Harness(
        import_path='assistant_bench.harnesses.terminus:Terminus',
        options=options.terminus,
        # Pins LiteLLM to its OpenAI-compatible provider.
        model_prefix='openai/',
    ),
}
