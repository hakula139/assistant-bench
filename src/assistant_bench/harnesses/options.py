"""The kwargs each system harness builds from a system and the family's settings."""

from typing import Any

from assistant_bench.catalog import System
from assistant_bench.families import Settings


def generate(system: System, settings: Settings) -> dict[str, Any]:
    """Return the kwargs that send `system`'s request to the endpoint."""
    assert settings.max_output_tokens is not None
    return {
        'system': system.name,
        'fingerprint': system.fingerprint,
        'effort': system.effort,
        'system_prompt': system.prompt,
        'max_output_tokens': settings.max_output_tokens,
    }


def terminus(system: System, settings: Settings) -> dict[str, Any]:
    """Return the kwargs that run `system` as terminus-2."""
    assert settings.max_output_tokens is not None
    values: dict[str, Any] = {
        'system': system.name,
        'fingerprint': system.fingerprint,
        'llm_call_kwargs': {'max_completion_tokens': settings.max_output_tokens},
        'record_terminal_session': False,
        'suppress_max_turns_warning': True,
    }
    if system.effort is not None:
        values['reasoning_effort'] = system.effort
    return values
