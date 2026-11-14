"""The kwargs each harness builds from a system's generation identity."""

from typing import Any, Literal

from pydantic import BaseModel

from assistant_bench.catalog import Api, SystemIdentity


ThinkingMode = Literal['anthropic-adaptive']


class ApiOptions(BaseModel):
    api: Api
    thinking_mode: ThinkingMode | None = None


def _thinking_mode(identity: SystemIdentity) -> dict[str, ThinkingMode]:
    if identity.api != 'anthropic-messages':
        return {}
    return {'thinking_mode': 'anthropic-adaptive'}


def generate(identity: SystemIdentity) -> dict[str, Any]:
    return {
        'api': identity.api,
        'effort': identity.effort,
        'system_prompt': identity.prompt,
        'max_output_tokens': identity.max_output_tokens,
    } | _thinking_mode(identity)


def terminus(identity: SystemIdentity) -> dict[str, Any]:
    values: dict[str, Any] = {
        'api': identity.api,
        'model_info': {
            'max_input_tokens': identity.context_window - identity.max_output_tokens,
            'max_output_tokens': identity.max_output_tokens,
        },
        'record_terminal_session': False,
    } | _thinking_mode(identity)
    if identity.api == 'openai-chat-completions':
        values['llm_call_kwargs'] = {
            'max_completion_tokens': identity.max_output_tokens
        }
    if identity.effort is not None:
        values['reasoning_effort'] = identity.effort
    return values


def claude_code(identity: SystemIdentity) -> dict[str, Any]:
    return {
        'version': identity.version,
        'reasoning_effort': identity.effort,
        'disable_web_search': True,
        'max_turns': None,
        'max_thinking_tokens': None,
        'max_output_tokens': identity.max_output_tokens,
    }


def codex(identity: SystemIdentity) -> dict[str, Any]:
    return {
        'version': identity.version,
        'reasoning_effort': identity.effort,
        'disable_web_search': True,
        'config': {
            'model_context_window': identity.context_window,
            'model_max_output_tokens': identity.max_output_tokens,
        },
    }
