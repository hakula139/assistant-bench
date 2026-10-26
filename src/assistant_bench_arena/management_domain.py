"""Typed private management operations for the canonical voting owner."""

from typing import assert_never

from pydantic import BaseModel, JsonValue

from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import Context
from assistant_bench_arena.management_models import (
    Activate,
    ExportPage,
    FamilyStatus,
    Freeze,
    Investigate,
    ManagementStatus,
    Moderate,
    Request,
    SealEvaluation,
    SealModeration,
    SealPublication,
    StageCase,
    StageEvaluation,
    StageModeration,
    StageNumerical,
    StageOutput,
    StagePoints,
    StagePolicy,
    StagePublication,
    StageRendered,
    StageSystem,
    Status,
)
from assistant_bench_arena.releases import (
    validate_case,
    validate_output,
    validate_presentation,
    validate_rendered,
)
from assistant_bench_arena.serialization import canonical, digest
from assistant_bench_arena.snapshots import Settlement
from assistant_bench_arena.transport import OwnerInputError


PRINCIPAL = 'management'


async def execute(ledger: Ledger, request: Request, now: int) -> JsonValue:
    """Use the owner's trusted clock and fixed principal for every domain command."""
    result: BaseModel | None = None

    match request:
        case Status():
            deployment = await ledger.status()

            if deployment is not None:
                result = ManagementStatus(
                    **deployment.model_dump(),
                    expiry_lag_ms=await ledger.maintenance.attribution_lag(now),
                )
        case FamilyStatus():
            result = await ledger.investigation.status(request.family)
        case StagePolicy():
            await ledger.stage_policy(request.policy_id, request.policy)
        case StageEvaluation():
            await ledger.stage_evaluation(request.evaluation)
        case StageCase():
            validate_case(request.case)

            await ledger.stage_case(request.evaluation_id, request.case)
        case StageSystem():
            await ledger.stage_system(request.evaluation_id, request.system)
        case StageOutput():
            validate_output(request.output)

            await ledger.stage_output(request.evaluation_id, request.output)
        case StageNumerical():
            numerical = request.numerical

            if numerical.section != 'settlements':
                raise OwnerInputError('The numerical section must contain settlements.')

            settlement = Settlement.model_validate_json(canonical(numerical.value))

            if numerical.key != digest(settlement):
                raise OwnerInputError(
                    'A settlement must bind its exact numerical value.'
                )

            await ledger.stage_numerical(request.evaluation_id, request.numerical)
        case SealEvaluation():
            await ledger.seal_evaluation(request.evaluation_id, now)
        case StagePublication():
            bundle = await ledger.read.evaluation(request.publication.evaluation_id)

            validate_presentation(bundle, request.publication)

            await ledger.stage_publication(request.publication)
        case StageRendered():
            publication = await ledger.read.publication_metadata(request.publication_id)
            validate_rendered(publication, request.output)

            await ledger.stage_rendered(request.publication_id, request.output)
        case SealPublication():
            await ledger.seal_publication(request.publication_id, now)
        case StagePoints():
            await ledger.stage_points(request.points, now)
        case Activate():
            result = await ledger.activate(
                Context(command_id=request.command_id, principal=PRINCIPAL, now=now),
                request.family,
                request.revision,
                request.evaluation_id,
                request.publication_id,
                request.point_id,
            )
        case Freeze():
            result = await ledger.freeze(request.export_id, request.family, now)
        case ExportPage():
            result = await ledger.page(
                request.export_id, request.section, request.cursor, limit=request.limit
            )
        case Investigate():
            result = await ledger.investigation.receipts(
                request.filters, now, request.after
            )
        case StageModeration():
            if request.batch.principal != PRINCIPAL or request.batch.created_at > now:
                raise OwnerInputError(
                    'A moderation plan must name its management principal.'
                )

            await ledger.stage_moderation(request.batch)
        case SealModeration():
            await ledger.seal_moderation(request.batch_id, now)
        case Moderate():
            result = await ledger.moderate(
                Context(command_id=request.command_id, principal=PRINCIPAL, now=now),
                request.batch_id,
                request.chunk_index,
            )
        case _:
            assert_never(request)

    return result.model_dump(mode='json') if result is not None else None
