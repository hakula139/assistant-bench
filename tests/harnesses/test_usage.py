from pathlib import Path
from typing import Any

import pytest

from assistant_bench.catalog import Api
from assistant_bench.harnesses.labels import Labels
from assistant_bench.harnesses.usage import (
    ATTEMPTS_FILE,
    Attempt,
    MeasuredUsage,
    append_attempt,
    measure,
    measured_usage,
)
from support import START


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


VALID = {'prompt_tokens': 120, 'completion_tokens': 40}


class TestAppendAttempt:
    @pytest.mark.parametrize(
        ('requests', 'expected'),
        [
            pytest.param(
                [{**measure('openai-chat-completions', None), 'responded': False}],
                [],
                id='no-response',
            ),
            pytest.param(
                [
                    {
                        **measure('openai-chat-completions', VALID),
                        'responded': True,
                        'response_model': None,
                    },
                    {'response_status': 'request_failed', 'latency_sec': 0.5},
                    {**measure('openai-chat-completions', []), 'responded': True},
                    {**measure('openai-chat-completions', None), 'responded': True},
                ],
                [MeasuredUsage(120, 0, 0, 40, None), None, None],
                id='requests',
            ),
        ],
    )
    def test_records_each_response_in_the_job_directory(
        self,
        logs_dir: Path,
        requests: list[dict[str, Any]],
        expected: list[MeasuredUsage | None],
    ) -> None:
        labels = Labels(system='acme-large', fingerprint='0' * 12)

        append_attempt(logs_dir, labels, START, requests)
        append_attempt(logs_dir, labels, START, requests)

        lines = (logs_dir.parent.parent / ATTEMPTS_FILE).read_text().splitlines()
        attempts = [Attempt.model_validate_json(line) for line in lines]
        assert [(item.trial, item.responses) for item in attempts] == [
            ('trial', expected)
        ] * 2
