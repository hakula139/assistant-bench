"""System harness names, each mapped to the Harbor agent that runs it.

Harbor records each trial's agent and environment in the job's `config.json`,
where `harbor job resume` reads them back, so an import path never changes.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from assistant_bench.catalog import Api, System, SystemIdentity
from assistant_bench.harnesses import options


@dataclass(frozen=True)
class Harness:
    """A Harbor agent and how the bench configures it."""

    import_path: str
    options: Callable[[SystemIdentity], dict[str, Any]]
    """Builds the agent's kwargs other than the trial labels."""

    apis: tuple[Api, ...] = (
        'openai-chat-completions',
        'openai-responses',
        'anthropic-messages',
    )
    harbor_loop: bool = False
    model_prefix: str = ''

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
        harbor_loop=True,
    ),
    'claude-code': Harness(
        import_path='assistant_bench.harnesses.installed:ClaudeCode',
        options=options.claude_code,
        apis=('anthropic-messages',),
        harbor_loop=True,
        model_prefix='anthropic/',
    ),
    'codex': Harness(
        import_path='assistant_bench.harnesses.installed:Codex',
        options=options.codex,
        apis=('openai-responses',),
        harbor_loop=True,
        model_prefix='openai/',
    ),
}


def fingerprint(identity: SystemIdentity) -> str:
    """Hash of what shapes the outputs. Votes pool only within one fingerprint.

    It covers the generation version, the model name, and every agent kwarg but
    the trial labels, so an agent config changes only with the fingerprint or
    the system's name.
    """
    harness = HARNESSES[identity.harness]
    encoded = json.dumps(
        {
            'harness': identity.harness,
            'model_name': identity.route,
            'options': harness.options(identity),
            'version': identity.generation.version(harness.harbor_loop, identity.api),
        },
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def agent_kwargs(system: System) -> dict[str, Any]:
    """Return the harness's kwargs with the labels that collection reads."""
    labels = {'system': system.name, 'fingerprint': fingerprint(system)}
    return labels | HARNESSES[system.harness].options(system)
