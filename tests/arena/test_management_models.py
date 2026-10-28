import json

import pytest
from pydantic import JsonValue, ValidationError

from assistant_bench_arena import management_models as management
from assistant_bench_arena.investigation_models import NetworkLookup, ReceiptFilter
from assistant_bench_arena.ledger_models import Moderation, Target
from assistant_bench_arena.serialization import canonical, digest
from ledger_fixtures import make_release


def management_requests() -> tuple[management.Request, ...]:
    release = make_release(cases=1, failing_case=0)
    evaluation = release.evaluation
    publication = release.publication
    moderation = Moderation(
        id='reviewed-plan',
        family='writing',
        principal='management',
        action='exclude',
        reason='Synthetic reviewed correction.',
        created_at=10,
        targets=(Target(receipt_seq=1, claim_version=2),),
    )
    return (
        management.Status(),
        management.FamilyStatus(family='writing'),
        management.StagePolicy(policy_id=evaluation.policy_id, policy=release.policy),
        management.StageEvaluation(evaluation=evaluation),
        management.StageCase(evaluation_id=evaluation.id, case=release.cases[0]),
        management.StageSystem(evaluation_id=evaluation.id, system=release.systems[0]),
        management.StageOutput(evaluation_id=evaluation.id, output=release.outputs[0]),
        management.StageNumerical(
            evaluation_id=evaluation.id, numerical=release.numerics[0]
        ),
        management.SealEvaluation(evaluation_id=evaluation.id),
        management.StagePublication(publication=publication),
        management.StageRendered(
            publication_id=publication.id, output=release.rendered[0]
        ),
        management.SealPublication(publication_id=publication.id),
        management.StagePoints(points=release.points),
        management.Activate(
            command_id='activate',
            family='writing',
            revision=0,
            evaluation_id=evaluation.id,
            publication_id=publication.id,
            point_id=release.points.id,
        ),
        management.Freeze(export_id='capture', family='writing'),
        management.ExportPage(export_id='capture', section='receipts'),
        management.Investigate(
            filters=ReceiptFilter(
                family='writing',
                network=NetworkLookup(version='test-key', kind='exact', tag='tag'),
            )
        ),
        management.StageModeration(batch=moderation),
        management.SealModeration(batch_id=moderation.id),
        management.Moderate(
            command_id='moderate', batch_id=moderation.id, chunk_index=0
        ),
    )


class TestParseRequest:
    def test_preserves_reviewed_command_wire_and_nested_release_identities(
        self,
    ) -> None:
        requests = management_requests()
        payloads: list[JsonValue] = [
            request.model_dump(mode='json') for request in requests
        ]

        restored = [
            management.parse_request(canonical(payload)) for payload in payloads
        ]

        assert restored == list(requests)
        assert all(
            type(actual) is type(expected)
            for actual, expected in zip(restored, requests, strict=True)
        )
        assert (
            digest(payloads)
            == '401f0afd3e287e8603ecf02b6faf3d82d60001887dc9ecffe190d710419e13fa'
        )

    @pytest.mark.parametrize(
        'payload',
        [
            {'operation': 'unknown'},
            {'operation': 'status', 'unexpected': True},
            {'operation': 'export_page', 'export_id': 'capture', 'section': 'private'},
            {
                'operation': 'export_page',
                'export_id': 'capture',
                'section': 'receipts',
                'limit': True,
            },
            {
                'operation': 'export_page',
                'export_id': 'capture',
                'section': 'receipts',
                'limit': '1',
            },
            {
                'operation': 'export_page',
                'export_id': 'capture',
                'section': 'receipts',
                'limit': 0,
            },
            {
                'operation': 'export_page',
                'export_id': 'capture',
                'section': 'receipts',
                'limit': 251,
            },
            {'operation': 'investigate', 'filters': {'family': '../private'}},
            {
                'operation': 'investigate',
                'filters': {
                    'family': 'writing',
                    'network': {'version': 'test-key', 'kind': 'raw', 'tag': 'tag'},
                },
            },
            {'operation': 'investigate', 'filters': {'family': 'writing'}, 'after': -1},
        ],
        ids=[
            'unknown-operation',
            'extra-field',
            'private-section',
            'boolean-limit',
            'string-limit',
            'zero-limit',
            'oversized-page',
            'invalid-family',
            'invalid-network',
            'negative-cursor',
        ],
    )
    def test_rejects_invalid_wire_inputs(self, payload: dict[str, JsonValue]) -> None:
        with pytest.raises(ValidationError):
            management.parse_request(json.dumps(payload))

    def test_enforces_the_request_bound_in_utf8_bytes(self) -> None:
        request = management.ExportPage(
            export_id='capture', section='receipts', cursor='界'
        )
        payload = canonical(request)
        bounded = payload + ' ' * (1_048_576 - len(payload.encode()))

        restored = management.parse_request(bounded)

        assert restored == request
        with pytest.raises(ValueError, match='native 1 MiB bound'):
            management.parse_request(bounded + ' ')
        oversized = canonical(request.model_copy(update={'cursor': '界' * 400_000}))
        assert len(oversized) < 1_048_576 < len(oversized.encode())
        with pytest.raises(ValueError, match='native 1 MiB bound'):
            management.parse_request(oversized)
