"""Stage reviewed immutable releases through the authoritative ledger seals."""

from pydantic import JsonValue, TypeAdapter

from assistant_bench.arena.management import Management
from assistant_bench_arena import management_models as command
from assistant_bench_arena.investigation_models import FamilyStatus
from assistant_bench_arena.ledger_models import Event, PointSnapshot
from assistant_bench_arena.read_models import FamilyState
from assistant_bench_arena.releases import Release
from assistant_bench_arena.serialization import digest


_FAMILY = TypeAdapter(FamilyStatus | None)
_EVENT = TypeAdapter(Event)
_NONE = TypeAdapter(type(None))


async def stage_release(management: Management, release: Release) -> None:
    """Keep the current family active until every replacement section is sealed."""
    evaluation_id = release.evaluation.id
    operations: tuple[command.Request, ...] = (
        command.StagePolicy(
            policy_id=release.evaluation.policy_id, policy=release.policy
        ),
        command.StageEvaluation(evaluation=release.evaluation),
        *(
            command.StageCase(evaluation_id=evaluation_id, case=row)
            for row in release.cases
        ),
        *(
            command.StageSystem(evaluation_id=evaluation_id, system=row)
            for row in release.systems
        ),
        *(
            command.StageOutput(evaluation_id=evaluation_id, output=row)
            for row in release.outputs
        ),
        *(
            command.StageNumerical(evaluation_id=evaluation_id, numerical=row)
            for row in release.numerics
        ),
        command.SealEvaluation(evaluation_id=evaluation_id),
        command.StagePublication(publication=release.publication),
        *(
            command.StageRendered(publication_id=release.publication.id, output=row)
            for row in release.rendered
        ),
        command.SealPublication(publication_id=release.publication.id),
        command.StagePoints(points=release.points),
    )

    for operation in operations:
        await management.domain(operation, _NONE)


async def publish_release(management: Management, release: Release) -> Event | None:
    """Preserve fitted points when only the approved presentation changes."""
    current = await management.domain(
        command.FamilyStatus(family=release.evaluation.family), _FAMILY
    )
    state = current.state if current else None

    if state is not None and (
        state.evaluation_id == release.evaluation.id
        and state.publication_id == release.publication.id
    ):
        return None

    await stage_release(management, release)
    point_id = (
        state.point_id
        if state is not None and state.evaluation_id == release.evaluation.id
        else release.points.id
    )

    return await _activate(
        management,
        state,
        release.evaluation.family,
        release.evaluation.id,
        release.publication.id,
        point_id,
    )


async def publish_points(
    management: Management, family: str, points: PointSnapshot
) -> Event | None:
    """Refresh sampling without changing the currently approved presentation."""
    if points.source_export_id is None:
        raise ValueError('Fitted points require a registered source export.')

    current = await management.domain(command.FamilyStatus(family=family), _FAMILY)

    if current is None or current.state.evaluation_id != points.evaluation_id:
        raise ValueError('The fitted evaluation is no longer active.')

    state = current.state
    if state.point_id == points.id:
        return None

    await management.domain(command.StagePoints(points=points), _NONE)

    return await _activate(
        management,
        state,
        state.family,
        state.evaluation_id,
        state.publication_id,
        points.id,
    )


async def _activate(
    management: Management,
    state: FamilyState | None,
    family: str,
    evaluation_id: str,
    publication_id: str,
    point_id: str,
) -> Event:
    values: dict[str, JsonValue] = {
        'family': family,
        'revision': state.revision if state else 0,
        'evaluation_id': evaluation_id,
        'publication_id': publication_id,
        'point_id': point_id,
    }
    request = command.Activate.model_validate(
        {'command_id': digest({'operation': 'activate', **values}), **values}
    )

    return await management.domain(request, _EVENT)
