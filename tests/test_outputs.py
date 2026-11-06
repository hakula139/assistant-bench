from typing import Any

import pytest

from assistant_bench.families import Family
from assistant_bench.outputs import pool_conflicts
from support import VOTES, make_output


TASKS = VOTES.model_copy(update={'source': 'tasks'})


class TestPoolConflicts:
    @pytest.mark.parametrize(
        ('outputs', 'family', 'expected'),
        [
            pytest.param(
                [
                    {'job': 'job-1', 'response_models': ('large-2026',)},
                    {'job': 'job-2', 'response_models': ('large-2026',)},
                ],
                VOTES,
                [],
                id='one-model',
            ),
            pytest.param(
                [
                    {'job': 'job-2', 'response_models': ('large-2026',)},
                    {'job': 'job-1', 'response_models': ('large-2026',)},
                    {'job': 'job-3', 'response_models': ('other-2026',)},
                    {'job': 'job-4', 'response_models': ('other-2026',)},
                ],
                TASKS,
                [
                    'System `s` reported several models: `large-2026` from job '
                    '`job-1`, `other-2026` from job `job-3`.'
                ],
                id='two-models',
            ),
            pytest.param(
                [
                    {'case': 'a', 'input_tokens': 300},
                    {'case': 'a', 'input_tokens': 301},
                    {'case': 'b', 'input_tokens': 50},
                    {'case': 'b', 'input_tokens': 52},
                    {'case': 'c', 'input_tokens': 1_000},
                    {'case': 'c', 'input_tokens': 1_020},
                ],
                VOTES,
                [],
                id='within-tolerance',
            ),
            pytest.param(
                [
                    {'case': 'a', 'input_tokens': 1_000},
                    {'case': 'a', 'input_tokens': 1_050},
                    {'case': 'b', 'input_tokens': 300},
                    {'case': 'b', 'input_tokens': 310},
                    {'case': 'c', 'input_tokens': 50},
                    {'case': 'c', 'input_tokens': 53},
                ],
                VOTES,
                [
                    'System `s` sent case `a` with 1,000 and 1,050 input tokens, one '
                    'of 3 cases whose counts differ.'
                ],
                id='beyond-tolerance',
            ),
            pytest.param(
                [{'input_tokens': 300}, {'input_tokens': 400}],
                TASKS,
                [],
                id='agent-runs',
            ),
            pytest.param(
                [{'input_tokens': 300}, {}, {'response_models': ('large-2026',)}],
                VOTES,
                [],
                id='missing-values',
            ),
            pytest.param(
                [
                    {'input_tokens': 300, 'fingerprint': 'a'},
                    {'input_tokens': 400, 'fingerprint': 'b'},
                ],
                VOTES,
                [],
                id='other-fingerprint',
            ),
        ],
    )
    def test_names_each_system_whose_outputs_pool_models(
        self, outputs: list[dict[str, Any]], family: Family, expected: list[str]
    ) -> None:
        records = [make_output(**fields) for fields in outputs]

        conflicts = pool_conflicts(family, records)

        assert conflicts == expected
