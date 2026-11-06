import json
from pathlib import Path
from typing import Any

import pytest

from assistant_bench.families import Family
from assistant_bench.messages import InputError
from assistant_bench.outputs import pool_conflicts, read_outputs
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
                    {'job': 'job-2', 'response_models': ('large-2026',)},
                    {'job': 'job-3', 'response_models': ('other-2026',)},
                ],
                TASKS,
                [
                    'System `s` reported several models: `large-2026` from job '
                    '`job-1` (1 output) and job `job-2` (2 outputs), `other-2026` from '
                    'job `job-3` (1 output).'
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
                    {'case': 'a', 'input_tokens': 1_030, 'job': 'job-1'},
                    {'case': 'a', 'input_tokens': 1_050, 'job': 'job-3'},
                    {'case': 'a', 'input_tokens': 1_000, 'job': 'job-2'},
                    {'case': 'b', 'input_tokens': 300},
                    {'case': 'b', 'input_tokens': 310},
                    {'case': 'c', 'input_tokens': 50},
                    {'case': 'c', 'input_tokens': 53},
                ],
                VOTES,
                [
                    'System `s` sent case `a` with 1,000 input tokens in job `job-2` '
                    'and 1,050 in job `job-3`, one of 3 cases whose counts differ.'
                ],
                id='beyond-tolerance',
            ),
            pytest.param(
                [
                    {'input_tokens': 5_000, 'job': 'job-1'},
                    {'input_tokens': 5_101, 'job': 'job-2'},
                ],
                VOTES,
                [
                    'System `s` sent case `c` with 5,000 input tokens in job `job-1` '
                    'and 5,101 in job `job-2`.'
                ],
                id='past-tolerance',
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


class TestReadOutputs:
    def test_names_a_file_an_older_collection_wrote(self, data_dir: Path) -> None:
        record = json.loads(make_output().model_dump_json())
        del record['response_models']
        path = data_dir / 'outputs' / 'f.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record) + '\n')

        with pytest.raises(InputError) as raised:
            read_outputs('f')

        assert str(raised.value) == (
            f'Outputs `{path}`: `response_models`: Field required.\n'
            'Run `uv run bench collect f` to rewrite it.'
        )
