"""System harness names, each mapped to the Harbor agent that runs it.

Harbor records each trial's agent and environment in the job's `config.json`,
where `harbor job resume` reads them back, so an import path never changes.
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
    `sends_prompt` says whether the agent sends a system's prompt, and
    `single_call` says whether it makes one request and never acts in the
    task's environment, so it cannot solve a task family's tasks. A job with
    only `single_call` harnesses runs in `HOST_ENVIRONMENT` without a container.
    """

    import_path: str
    options: Callable[[System, Settings], dict[str, Any]]
    model_prefix: str = ''
    sends_prompt: bool = False
    single_call: bool = False


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
