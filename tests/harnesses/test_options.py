from collections.abc import Callable
from typing import Any

import pytest

from assistant_bench.catalog import SystemIdentity
from assistant_bench.harnesses import options
from support import make_system


MESSAGES = {'api': 'anthropic-messages', 'thinking_mode': 'anthropic-adaptive'}


@pytest.mark.parametrize(
    ('build', 'fields', 'kwargs'),
    [
        (
            options.generate,
            {'prompt': 'Be brief.'},
            {
                'api': 'openai-chat-completions',
                'effort': 'medium',
                'system_prompt': 'Be brief.',
                'max_output_tokens': 64,
            },
        ),
        (
            options.generate,
            {'effort': None, 'prompt': None},
            {
                'api': 'openai-chat-completions',
                'effort': None,
                'system_prompt': None,
                'max_output_tokens': 64,
            },
        ),
        (
            options.generate,
            MESSAGES | {'effort': 'high'},
            MESSAGES
            | {'effort': 'high', 'system_prompt': None, 'max_output_tokens': 64},
        ),
        (
            options.terminus,
            {},
            {
                'api': 'openai-chat-completions',
                'llm_call_kwargs': {'max_completion_tokens': 64},
                'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
                'reasoning_effort': 'medium',
                'record_terminal_session': False,
            },
        ),
        (
            options.terminus,
            {'effort': None},
            {
                'api': 'openai-chat-completions',
                'llm_call_kwargs': {'max_completion_tokens': 64},
                'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
                'record_terminal_session': False,
            },
        ),
        (
            options.terminus,
            {
                'api': 'openai-responses',
                'effort': 'max',
                'max_output_tokens': 32768,
                'context_window': 131072,
            },
            {
                'api': 'openai-responses',
                'model_info': {'max_input_tokens': 98304, 'max_output_tokens': 32768},
                'reasoning_effort': 'max',
                'record_terminal_session': False,
            },
        ),
        (
            options.terminus,
            MESSAGES | {'effort': 'high'},
            MESSAGES
            | {
                'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
                'reasoning_effort': 'high',
                'record_terminal_session': False,
            },
        ),
    ],
    ids=[
        'generate',
        'generate-without-effort-or-prompt',
        'generate-messages',
        'terminus-chat',
        'terminus-without-effort',
        'terminus-responses',
        'terminus-messages',
    ],
)
def test_builds_each_harness_kwargs_from_the_identity(
    build: Callable[[SystemIdentity], dict[str, Any]],
    fields: dict[str, Any],
    kwargs: dict[str, Any],
) -> None:
    system = make_system(**fields)

    built = build(system)

    assert built == kwargs
