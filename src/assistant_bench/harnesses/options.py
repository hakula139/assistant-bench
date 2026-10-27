"""The kwargs each harness builds from a system's generation identity."""

from typing import Any, Self

from pydantic import BaseModel, model_validator

from assistant_bench.catalog import Api, SystemIdentity, ThinkingMode


class ApiOptions(BaseModel):
    api: Api = 'openai-chat-completions'
    thinking_mode: ThinkingMode | None = None

    @model_validator(mode='after')
    def _check_thinking(self) -> Self:
        if self.api == 'anthropic-messages' and self.thinking_mode is None:
            raise ValueError('Native Messages requires a resolved thinking mode.')
        return self


def generate(identity: SystemIdentity) -> dict[str, Any]:
    values: dict[str, Any] = {
        'api': identity.api,
        'effort': identity.effort,
        'system_prompt': identity.prompt,
        'max_output_tokens': identity.max_output_tokens,
    }
    if identity.api == 'anthropic-messages':
        values['thinking_mode'] = identity.thinking_mode
    return values


def terminus(identity: SystemIdentity) -> dict[str, Any]:
    values: dict[str, Any] = {
        'use_responses_api': identity.api == 'openai-responses',
        'model_info': {
            'max_input_tokens': identity.context_window - identity.max_output_tokens,
            'max_output_tokens': identity.max_output_tokens,
        },
        'record_terminal_session': False,
    }
    if identity.api == 'anthropic-messages':
        values |= {'api': identity.api, 'thinking_mode': identity.thinking_mode}

    if identity.api == 'openai-chat-completions':
        values['llm_call_kwargs'] = {
            'max_completion_tokens': identity.max_output_tokens
        }
    if identity.effort is not None:
        values['reasoning_effort'] = identity.effort
    return values
