"""The kwargs each harness builds from its resolved system."""

from typing import Any, Self

from pydantic import BaseModel, model_validator

from assistant_bench.catalog import Api, System, ThinkingMode


class ApiOptions(BaseModel):
    api: Api = 'openai-chat-completions'
    thinking_mode: ThinkingMode | None = None

    @model_validator(mode='after')
    def _check_thinking(self) -> Self:
        if self.api == 'anthropic-messages' and self.thinking_mode is None:
            raise ValueError('Native Messages requires a resolved thinking mode.')
        return self


def generate(system: System) -> dict[str, Any]:
    return (
        _labels(system)
        | {
            'api': system.api,
            'effort': system.effort,
            'system_prompt': system.prompt,
            'max_output_tokens': system.max_output_tokens,
        }
        | (
            {'thinking_mode': system.thinking_mode}
            if system.api == 'anthropic-messages'
            else {}
        )
    )


def terminus(system: System) -> dict[str, Any]:
    values = _labels(system) | {
        'use_responses_api': system.api == 'openai-responses',
        'model_info': {
            'max_input_tokens': system.context_window - system.max_output_tokens,
            'max_output_tokens': system.max_output_tokens,
        },
        'record_terminal_session': False,
    }
    if system.api == 'anthropic-messages':
        values |= {'api': system.api, 'thinking_mode': system.thinking_mode}

    if system.api == 'openai-chat-completions':
        values['llm_call_kwargs'] = {'max_completion_tokens': system.max_output_tokens}
    if system.effort is not None:
        values['reasoning_effort'] = system.effort
    return values


def _labels(system: System) -> dict[str, Any]:
    return {'system': system.name, 'fingerprint': system.fingerprint}
