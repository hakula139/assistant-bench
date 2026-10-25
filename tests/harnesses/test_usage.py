from typing import Any

import pytest

from assistant_bench.catalog import Api
from assistant_bench.harnesses.usage import measured_usage


class TestMeasuredUsage:
    @pytest.mark.parametrize(
        ('api', 'reported'),
        [
            ('openai-chat-completions', []),
            ('anthropic-messages', {'input_tokens': True, 'output_tokens': 0}),
            ('anthropic-messages', {'input_tokens': 20.0, 'output_tokens': 0}),
            ('anthropic-messages', {'input_tokens': '20', 'output_tokens': 0}),
            (
                'openai-chat-completions',
                {
                    'prompt_tokens': 120,
                    'completion_tokens': 40,
                    'prompt_tokens_details': 2,
                },
            ),
            (
                'openai-chat-completions',
                {
                    'prompt_tokens': 120,
                    'completion_tokens': 40,
                    'prompt_tokens_details': {'cached_tokens': 121},
                },
            ),
            (
                'openai-responses',
                {
                    'input_tokens': 120,
                    'output_tokens': 40,
                    'input_tokens_details': {'cached_tokens': 121},
                },
            ),
            (
                'openai-responses',
                {'input_tokens': 120, 'output_tokens': 40, 'total_tokens': 161},
            ),
            (
                'anthropic-messages',
                {
                    'input_tokens': 20,
                    'output_tokens': 40,
                    'cache_creation_input_tokens': 10,
                    'cache_creation': {
                        'ephemeral_5m_input_tokens': 4,
                        'ephemeral_1h_input_tokens': 5,
                    },
                },
            ),
            (
                'anthropic-messages',
                {
                    'input_tokens': 20,
                    'output_tokens': 40,
                    'cache_creation_input_tokens': 10,
                    'cache_creation': {'ephemeral_5m_input_tokens': 10},
                },
            ),
        ],
        ids=[
            'non-object',
            'boolean-counter',
            'integral-float-counter',
            'text-counter',
            'non-object-details',
            'chat-cache-exceeds-input',
            'responses-cache-exceeds-input',
            'inconsistent-total',
            'inconsistent-cache-ttl-total',
            'missing-cache-ttl-counter',
        ],
    )
    def test_leaves_inconsistent_counts_unknown(
        self, api: Api, reported: object
    ) -> None:
        measured = measured_usage(api, reported)

        assert measured is None

    @pytest.mark.parametrize(
        ('short', 'long', 'unpriced'),
        [(10, 0, False), (7, 3, True)],
        ids=['five-minute-write', 'mixed-ttl-write'],
    )
    def test_retains_native_cache_durations_and_flags_an_unpriced_duration(
        self, short: int, long: int, unpriced: bool
    ) -> None:
        reported: dict[str, Any] = {
            'input_tokens': 20,
            'cache_read_input_tokens': 100,
            'cache_creation_input_tokens': 10,
            'output_tokens': 40,
            'cache_creation': {
                'ephemeral_5m_input_tokens': short,
                'ephemeral_1h_input_tokens': long,
            },
        }

        measured = measured_usage('anthropic-messages', reported)
        assert measured is not None

        assert measured.details == {
            'input_tokens': 130,
            'cache_tokens': 100,
            'cache_write_tokens': 10,
            'output_tokens': 40,
            'reasoning_tokens': None,
            'cache_write_unpriced': unpriced,
        }
        assert (
            measured.harbor.prompt_tokens,
            measured.harbor.cache_tokens,
            measured.harbor.completion_tokens,
        ) == (130, 100, 40)
