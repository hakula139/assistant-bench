from typing import Any

import pytest
from pydantic import ValidationError

from assistant_bench.harnesses import options
from support import make_system


class TestApiOptions:
    def test_rejects_native_requests_without_a_resolved_thinking_mode(self) -> None:
        with pytest.raises(ValidationError, match='resolved thinking mode'):
            options.ApiOptions(api='anthropic-messages')


class TestGenerate:
    def test_passes_the_native_thinking_contract_without_openai_options(self) -> None:
        system = make_system(
            api='anthropic-messages', thinking_mode='anthropic-adaptive', effort='high'
        )

        kwargs = options.generate(system)

        assert kwargs == {
            'api': 'anthropic-messages',
            'thinking_mode': 'anthropic-adaptive',
            'effort': 'high',
            'system_prompt': None,
            'max_output_tokens': 64,
        }

    @pytest.mark.parametrize(
        ('fields', 'settings'),
        [
            pytest.param(
                {'prompt': 'Be brief.'},
                {'effort': 'medium', 'system_prompt': 'Be brief.'},
                id='every-setting',
            ),
            pytest.param(
                {'effort': None, 'prompt': None},
                {'effort': None, 'system_prompt': None},
                id='missing-settings',
            ),
        ],
    )
    def test_builds_the_request_settings(
        self, fields: dict[str, Any], settings: dict[str, Any]
    ) -> None:
        system = make_system(**fields)

        kwargs = options.generate(system)

        assert kwargs == {
            'api': 'openai-chat-completions',
            **settings,
            'max_output_tokens': 64,
        }


class TestTerminus:
    def test_uses_native_messages_with_the_json_terminal_protocol(self) -> None:
        system = make_system(
            api='anthropic-messages',
            thinking_mode='anthropic-adaptive',
            effort='high',
            harness='terminus-2',
        )

        kwargs = options.terminus(system)

        assert kwargs == {
            'api': 'anthropic-messages',
            'thinking_mode': 'anthropic-adaptive',
            'use_responses_api': False,
            'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
            'reasoning_effort': 'high',
            'record_terminal_session': False,
        }

    def test_builds_the_kwargs_a_system_yields(self) -> None:
        system = make_system(harness='terminus-2')

        kwargs = options.terminus(system)

        assert kwargs == {
            'use_responses_api': False,
            'llm_call_kwargs': {'max_completion_tokens': 64},
            'model_info': {'max_input_tokens': 8128, 'max_output_tokens': 64},
            'reasoning_effort': 'medium',
            'record_terminal_session': False,
        }

    def test_omits_the_effort_the_catalog_resolves_none(self) -> None:
        system = make_system(harness='terminus-2', effort=None)

        kwargs = options.terminus(system)

        assert 'reasoning_effort' not in kwargs

    def test_uses_native_responses_limits_without_chat_only_kwargs(self) -> None:
        system = make_system(
            api='openai-responses',
            harness='terminus-2',
            effort='max',
            max_output_tokens=32768,
            context_window=131072,
        )

        kwargs = options.terminus(system)

        assert kwargs == {
            'use_responses_api': True,
            'model_info': {'max_input_tokens': 98304, 'max_output_tokens': 32768},
            'reasoning_effort': 'max',
            'record_terminal_session': False,
        }
