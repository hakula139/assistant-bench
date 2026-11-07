import hashlib
import json
from typing import Any

import pytest

from assistant_bench.families import Family
from assistant_bench.messages import InputError
from assistant_bench.outputs import pool_conflicts, read_outputs, write_outputs
from assistant_bench.state import Collection, State
from support import START, VOTES, make_output


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
                    {'job': 'job-4', 'response_models': ('large-2026',)},
                    {'job': 'job-3', 'response_models': ('other-2026',)},
                ],
                TASKS,
                [
                    'System `s` reported several models: `large-2026` from job '
                    '`job-1` (1 output), job `job-2` (2 outputs) and job `job-4` (1 '
                    'output), `other-2026` from job `job-3` (1 output).'
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
                [{'input_tokens': 1_000}, {'input_tokens': 1_050}],
                VOTES,
                [
                    'System `s` sent case `c` with 1,000 and 1,050 input tokens in job '
                    '`j`.'
                ],
                id='one-job',
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
    def test_names_an_invalid_record_from_a_stored_collection(
        self, private_state: State
    ) -> None:
        record = json.loads(make_output().model_dump_json())
        del record['response_models']
        value = json.dumps(record)
        private_state.begin_collection(
            Collection(
                id='stored',
                family='f',
                expected_count=1,
                manifest_digest=hashlib.sha256(f'{value}\n'.encode()).hexdigest(),
                created_at=START.isoformat(),
                code_revision=None,
                case_revision=None,
                system_names_json=None,
            )
        )
        private_state.stage_output('stored', 0, value)
        private_state.seal_collection('stored')
        assert private_state.activate_collection('stored', None)

        with pytest.raises(InputError) as raised:
            read_outputs('f', private_state)

        assert str(raised.value) == (
            'Outputs of `f`: `response_models`: Field required.\n'
            'Run `uv run bench collect f` to rewrite it.'
        )


class TestWriteOutputs:
    def test_preserves_pinned_outputs_and_refuses_a_stale_writer(
        self, private_state: State
    ) -> None:
        first = make_output(text='Original answer.\r\n')
        following = make_output(text='New answer.\n')
        assert read_outputs('f', private_state) == []
        original = write_outputs(
            'f',
            [first],
            private_state,
            previous=None,
            code_revision='a' * 40,
            case_revision='b' * 40,
            system_names={'s': 'System S'},
        )
        pinned = private_state.output_generation('f')
        current = write_outputs(
            'f',
            [following],
            private_state,
            previous=original,
            code_revision='c' * 40,
            case_revision='d' * 40,
            system_names={'s': 'Renamed System'},
        )

        with pytest.raises(InputError, match='changed its active outputs'):
            write_outputs(
                'f',
                [make_output(text='Stale answer.')],
                private_state,
                previous=original,
                code_revision='a' * 40,
                case_revision='b' * 40,
                system_names={'s': 'System S'},
            )

        assert read_outputs('f', private_state, generation=pinned) == [first]
        assert read_outputs('f', private_state) == [following]
        assert private_state.output_generation('f').id == current
        assert private_state.collection(original).system_names_json == (
            '{"s": "System S"}'
        )
