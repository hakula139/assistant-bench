import asyncio
from collections.abc import Iterator

import pytest

from assistant_bench_arena import management_domain as management
from assistant_bench_arena.ledger_models import BootstrapConfiguration
from assistant_bench_arena.serialization import digest
from ledger_fixtures import make_release
from ledger_support import D1Runtime


RETENTION_MS = 60 * 86_400_000


@pytest.fixture(scope='session')
def d1_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[D1Runtime]:
    with D1Runtime(tmp_path_factory.mktemp('management-runtime')).owner() as runtime:
        yield runtime


class TestExecute:
    @pytest.mark.parametrize(
        ('mutation', 'operation', 'table'),
        [
            ('private-case', 'stage_case', 'evaluation_cases'),
            ('unapproved-case', 'stage_case', 'evaluation_cases'),
            ('unapproved-output', 'stage_output', 'evaluation_outputs'),
            ('missing-output-approval', 'stage_output', 'evaluation_outputs'),
            ('missing-presentation-approval', 'stage_publication', 'publications'),
            ('changed-presentation', 'stage_publication', 'publications'),
            ('unapproved-render', 'stage_rendered', 'rendered_outputs'),
            ('changed-render', 'stage_rendered', 'rendered_outputs'),
            ('unknown-numerical-section', 'stage_numerical', 'evaluation_numerics'),
            ('private-numerical-value', 'stage_numerical', 'evaluation_numerics'),
        ],
        ids=lambda value: value,
    )
    def test_public_preflight_rejects_private_content_before_any_staged_write(
        self,
        d1_runtime: D1Runtime,
        mutation: str,
        operation: str,
        table: str,
    ) -> None:
        async def scenario() -> None:
            async with d1_runtime.client() as (ledger, transport):
                await transport.reset()
                await transport.bootstrap(
                    BootstrapConfiguration(
                        namespace='public',
                        quotas=(),
                        attribution_ms=RETENTION_MS,
                    )
                )
                release = make_release(cases=1, failing_case=0)
                evaluation = release.evaluation
                requests: list[management.Request] = [
                    management.StagePolicy(
                        policy_id=evaluation.policy_id, policy=release.policy
                    ),
                    management.StageEvaluation(evaluation=evaluation),
                    *(
                        management.StageSystem(
                            evaluation_id=evaluation.id, system=system
                        )
                        for system in release.systems
                    ),
                    *(
                        management.StageCase(evaluation_id=evaluation.id, case=case)
                        for case in release.cases
                    ),
                    *(
                        management.StageOutput(
                            evaluation_id=evaluation.id, output=output
                        )
                        for output in release.outputs
                    ),
                    *(
                        management.StageNumerical(
                            evaluation_id=evaluation.id, numerical=numerical
                        )
                        for numerical in release.numerics
                    ),
                    management.SealEvaluation(evaluation_id=evaluation.id),
                    management.StagePublication(publication=release.publication),
                    *(
                        management.StageRendered(
                            publication_id=release.publication.id, output=output
                        )
                        for output in release.rendered
                    ),
                    management.SealPublication(publication_id=release.publication.id),
                    management.StagePoints(points=release.points),
                    management.Activate(
                        command_id='activate',
                        family='writing',
                        revision=0,
                        evaluation_id=evaluation.id,
                        publication_id=release.publication.id,
                        point_id=release.points.id,
                    ),
                ]
                target = next(
                    index
                    for index, request in enumerate(requests)
                    if request.operation == operation
                )

                for request in requests[:target]:
                    await management.execute(
                        ledger,
                        management.parse_request(request.model_dump_json()),
                        10,
                    )

                request = requests[target]
                invalid: management.Request

                match request:
                    case management.StageCase():
                        changed = (
                            request.case.model_copy(update={'source': 'private'})
                            if mutation == 'private-case'
                            else request.case.model_copy(
                                update={
                                    'metadata': request.case.metadata.model_copy(
                                        update={'approval': None}
                                    )
                                }
                            )
                        )
                        invalid = request.model_copy(update={'case': changed})
                    case management.StageOutput():
                        changed_output = (
                            request.output.model_copy(update={'approved_public': False})
                            if mutation == 'unapproved-output'
                            else request.output.model_copy(
                                update={
                                    'metadata': request.output.metadata.model_copy(
                                        update={'approval': None}
                                    )
                                }
                            )
                        )
                        invalid = request.model_copy(update={'output': changed_output})
                    case management.StagePublication():
                        publication = request.publication

                        if mutation == 'missing-presentation-approval':
                            publication = publication.model_copy(
                                update={'approval': None}
                            )
                        else:
                            documents = dict(publication.presentation.cases)
                            key = next(iter(documents))
                            documents[key] = documents[key].model_copy(
                                update={'instruction': 'Private instruction.'}
                            )
                            presentation = publication.presentation.model_copy(
                                update={'cases': documents}
                            )
                            publication = publication.model_copy(
                                update={'presentation': presentation}
                            )

                        invalid = request.model_copy(
                            update={'publication': publication}
                        )
                    case management.StageRendered():
                        rendered = request.output.model_copy(
                            update=(
                                {'approved_public': False}
                                if mutation == 'unapproved-render'
                                else {
                                    'content': 'Private rendered content.',
                                    'content_digest': digest(
                                        'Private rendered content.'
                                    ),
                                }
                            )
                        )
                        invalid = request.model_copy(update={'output': rendered})
                    case management.StageNumerical():
                        numerical = request.numerical.model_copy(
                            update=(
                                {'section': 'private'}
                                if mutation == 'unknown-numerical-section'
                                else {'value': {'private_note': 'Private note.'}}
                            )
                        )
                        invalid = request.model_copy(update={'numerical': numerical})
                    case _:
                        raise AssertionError(request.operation)

                before = await transport.rows(f'SELECT * FROM {table}')
                parsed = management.parse_request(invalid.model_dump_json())

                with pytest.raises(ValueError):
                    await management.execute(ledger, parsed, 11)

                assert await transport.rows(f'SELECT * FROM {table}') == before
                assert await transport.rows('SELECT * FROM family_state') == []

                for request in requests[target:]:
                    await management.execute(
                        ledger,
                        management.parse_request(request.model_dump_json()),
                        12,
                    )

                state = await ledger.read.family('writing')
                assert state is not None
                assert state.evaluation_id == evaluation.id
                publication, outputs = await ledger.read.publication(
                    release.publication.id
                )
                assert publication == release.publication
                assert {output.output_key: output for output in outputs} == {
                    output.output_key: output for output in release.rendered
                }

        asyncio.run(scenario())
