from typing import Any

import pytest

from assistant_bench.harnesses import options
from support import make_system


MESSAGES = {'api': 'anthropic-messages', 'thinking_mode': 'anthropic-adaptive'}


class TestGenerate:
    @pytest.mark.parametrize(
        ('fields', 'kwargs'),
        [
            (
                {'prompt': 'Be brief.'},
                {
                    'api': 'openai-chat-completions',
                    'effort': 'medium',
                    'system_prompt': 'Be brief.',
                    'max_output_tokens': 64,
                },
            ),
            (
                {'effort': None, 'prompt': None},
                {
                    'api': 'openai-chat-completions',
                    'effort': None,
                    'system_prompt': None,
                    'max_output_tokens': 64,
                },
            ),
            (
                MESSAGES | {'effort': 'high'},
                MESSAGES
                | {'effort': 'high', 'system_prompt': None, 'max_output_tokens': 64},
            ),
        ],
        ids=['chat', 'without-effort-or-prompt', 'messages'],
    )
    def test_builds_the_kwargs_from_the_identity(
        self, fields: dict[str, Any], kwargs: dict[str, Any]
    ) -> None:
        system = make_system(**fields)

        built = options.generate(system)

        assert built == kwargs


class TestTerminus:
    @pytest.mark.parametrize(
        ('fields', 'kwargs'),
        [
            (
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
                {'effort': None},
                {
                    'api': 'openai-chat-completions',
                    'llm_call_kwargs': {'max_completion_tokens': 64},
                    'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
                    'record_terminal_session': False,
                },
            ),
            (
                {
                    'api': 'openai-responses',
                    'effort': 'max',
                    'max_output_tokens': 32768,
                    'context_window': 131072,
                },
                {
                    'api': 'openai-responses',
                    'model_info': {
                        'max_input_tokens': 98304,
                        'max_output_tokens': 32768,
                    },
                    'reasoning_effort': 'max',
                    'record_terminal_session': False,
                },
            ),
            (
                MESSAGES | {'effort': 'high'},
                MESSAGES
                | {
                    'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
                    'reasoning_effort': 'high',
                    'record_terminal_session': False,
                },
            ),
        ],
        ids=['chat', 'without-effort', 'responses', 'messages'],
    )
    def test_builds_the_kwargs_from_the_identity(
        self, fields: dict[str, Any], kwargs: dict[str, Any]
    ) -> None:
        system = make_system(**fields)

        built = options.terminus(system)

        assert built == kwargs
