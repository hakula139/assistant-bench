import json
from collections.abc import Callable
from pathlib import Path

import jsonschema
import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic.json_schema import JsonSchemaValue
from typing_extensions import TypeForm

from assistant_bench_arena import api_models as api
from assistant_bench_arena.models import Defects, FamilyView, Pane
from support import PROSE_DEFECTS


FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/arena.schema.json'


def input_schemas() -> dict[str, JsonSchemaValue]:
    models: dict[str, TypeForm[object]] = {
        'pane': Pane,
        'familyView': FamilyView,
        'pairView': api.PairView,
        'sessionView': api.SessionView,
        'statusView': api.StatusView,
        'admissionView': api.AdmissionView,
        'admissionRequest': api.AdmissionRequest,
        'admissionReplay': api.AdmissionReplay,
        'startSessionRequest': api.StartSession,
        'resumeSessionRequest': api.ResumeSession,
        'receiptQuery': api.ReceiptQuery,
        'voteRequest': api.VoteRequest,
        'voteAcknowledgment': api.VoteAcknowledgment,
        'withdrawRequest': api.WithdrawRequest,
        'freshnessView': api.FreshnessView,
        'errorView': api.ErrorView,
        'taggedDefects': Defects,
    }

    return {
        name: TypeAdapter[object](model).json_schema(mode='validation')
        for name, model in models.items()
    }


class TestApiContract:
    @pytest.mark.parametrize('invalid', [None, 'unknown', 'punctuation'])
    def test_roundtrips_all_writing_issues_and_rejects_unknown_or_retired_codes(
        self, invalid: str | None
    ) -> None:
        tags = list(PROSE_DEFECTS) if invalid is None else [invalid]
        payload = {'a': tags, 'b': tags}
        schema = Defects.model_json_schema(mode='validation')

        if invalid is None:
            parsed = Defects.model_validate_json(json.dumps(payload))

            assert parsed.a == parsed.b == frozenset(PROSE_DEFECTS)
            assert Defects.model_validate_json(parsed.model_dump_json()) == parsed
            jsonschema.validate(json.loads(parsed.model_dump_json()), schema)
        else:
            with pytest.raises(ValidationError):
                Defects.model_validate_json(json.dumps(payload))
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(payload, schema)

    @pytest.mark.parametrize(
        ('model', 'field', 'base'),
        [
            (
                api.StartSession,
                'family',
                {'command_id': '10000000-0000-4000-8000-000000000001'},
            ),
            (
                FamilyView,
                'name',
                {'aspect': None, 'display_name': 'Synthetic', 'defects': []},
            ),
        ],
        ids=['start', 'family-view'],
    )
    @pytest.mark.parametrize(
        ('value', 'valid'),
        [
            ('a', True),
            ('family-1', True),
            ('a' * 256, True),
            ('', False),
            ('a\n', False),
            ('../outside', False),
            ('Uppercase', False),
            ('a' * 257, False),
        ],
        ids=[
            'minimal',
            'slug',
            'maximum',
            'empty',
            'newline',
            'traversal',
            'uppercase',
            'too-long',
        ],
    )
    def test_native_and_json_schema_enforce_the_same_family_name_boundary(
        self,
        model: TypeForm[object],
        field: str,
        base: dict[str, JsonValue],
        value: str,
        valid: bool,
    ) -> None:
        adapter = TypeAdapter[object](model)
        payload = base | {field: value}
        schema = adapter.json_schema(mode='validation')

        if valid:
            adapter.validate_json(json.dumps(payload))
            jsonschema.validate(payload, schema)
        else:
            with pytest.raises(ValidationError):
                adapter.validate_json(json.dumps(payload))
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(payload, schema)

    def test_matches_the_browser_schema_fixture(
        self, stale_fixture: Callable[[Path, str], None]
    ) -> None:
        current = input_schemas()

        if not FIXTURE.exists() or json.loads(FIXTURE.read_text()) != current:
            stale_fixture(
                FIXTURE, json.dumps(current, ensure_ascii=False, indent=2) + '\n'
            )
