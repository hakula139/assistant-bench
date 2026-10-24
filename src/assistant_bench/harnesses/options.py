"""The kwargs each harness builds from its resolved system."""

from typing import Any

from assistant_bench.catalog import System


def generate(system: System) -> dict[str, Any]:
    return _labels(system) | {
        'api': system.api,
        'effort': system.effort,
        'system_prompt': system.prompt,
        'max_output_tokens': system.max_output_tokens,
    }


def terminus(system: System) -> dict[str, Any]:
    values = _labels(system) | {
        'use_responses_api': system.api == 'openai-responses',
        'model_info': {
            'max_input_tokens': system.context_window - system.max_output_tokens,
            'max_output_tokens': system.max_output_tokens,
        },
        'record_terminal_session': False,
    }

    if system.api == 'openai-chat-completions':
        values['llm_call_kwargs'] = {'max_completion_tokens': system.max_output_tokens}
    if system.effort is not None:
        values['reasoning_effort'] = system.effort
    return values


def _labels(system: System) -> dict[str, Any]:
    return {'system': system.name, 'fingerprint': system.fingerprint}
