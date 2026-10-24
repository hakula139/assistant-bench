"""Typed private management operations for the canonical voting owner."""

from typing import Annotated, Literal, assert_never

from pydantic import BaseModel, Field, JsonValue, NonNegativeInt, TypeAdapter

from assistant_bench_arena.investigation import ReceiptFilter
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    Case,
    Context,
    DeploymentStatus,
    Evaluation,
    ExportSection,
    Identifier,
    Moderation,
    NumericalInput,
    Output,
    PointSnapshot,
    Publication,
    RenderedOutput,
    System,
)
from assistant_bench_arena.models import Record
from assistant_bench_arena.releases import (
    validate_case,
    validate_output,
    validate_presentation,
    validate_rendered,
)
from assistant_bench_arena.serialization import canonical, digest
from assistant_bench_arena.snapshots import AnalysisPolicy, Settlement


PRINCIPAL = 'management'


class Status(Record):
    operation: Literal['status'] = 'status'


class ManagementStatus(DeploymentStatus):
    expiry_lag_ms: NonNegativeInt
    """Milliseconds past the oldest pending network-association expiry."""


class FamilyStatus(Record):
    operation: Literal['family_status'] = 'family_status'
    family: Identifier


class StagePolicy(Record):
    operation: Literal['stage_policy'] = 'stage_policy'
    policy_id: Identifier
    policy: AnalysisPolicy


class StageEvaluation(Record):
    operation: Literal['stage_evaluation'] = 'stage_evaluation'
    evaluation: Evaluation


class StageCase(Record):
    operation: Literal['stage_case'] = 'stage_case'
    evaluation_id: Identifier
    case: Case


class StageSystem(Record):
    operation: Literal['stage_system'] = 'stage_system'
    evaluation_id: Identifier
    system: System


class StageOutput(Record):
    operation: Literal['stage_output'] = 'stage_output'
    evaluation_id: Identifier
    output: Output


class StageNumerical(Record):
    operation: Literal['stage_numerical'] = 'stage_numerical'
    evaluation_id: Identifier
    numerical: NumericalInput


class SealEvaluation(Record):
    operation: Literal['seal_evaluation'] = 'seal_evaluation'
    evaluation_id: Identifier


class StagePublication(Record):
    operation: Literal['stage_publication'] = 'stage_publication'
    publication: Publication


class StageRendered(Record):
    operation: Literal['stage_rendered'] = 'stage_rendered'
    publication_id: Identifier
    output: RenderedOutput


class SealPublication(Record):
    operation: Literal['seal_publication'] = 'seal_publication'
    publication_id: Identifier


class StagePoints(Record):
    operation: Literal['stage_points'] = 'stage_points'
    points: PointSnapshot


class Activate(Record):
    operation: Literal['activate'] = 'activate'
    command_id: Identifier
    family: Identifier
    revision: NonNegativeInt
    evaluation_id: Identifier
    publication_id: Identifier
    point_id: Identifier


class Freeze(Record):
    operation: Literal['freeze'] = 'freeze'
    export_id: Identifier
    family: Identifier


class ExportPage(Record):
    operation: Literal['export_page'] = 'export_page'
    export_id: Identifier
    section: ExportSection
    cursor: str | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=250)] = 250


class Investigate(Record):
    operation: Literal['investigate'] = 'investigate'
    filters: ReceiptFilter
    after: NonNegativeInt = 0


class StageModeration(Record):
    operation: Literal['stage_moderation'] = 'stage_moderation'
    batch: Moderation


class SealModeration(Record):
    operation: Literal['seal_moderation'] = 'seal_moderation'
    batch_id: Identifier


class Moderate(Record):
    operation: Literal['moderate'] = 'moderate'
    command_id: Identifier
    batch_id: Identifier
    chunk_index: NonNegativeInt


type Request = Annotated[
    Status
    | FamilyStatus
    | StagePolicy
    | StageEvaluation
    | StageCase
    | StageSystem
    | StageOutput
    | StageNumerical
    | SealEvaluation
    | StagePublication
    | StageRendered
    | SealPublication
    | StagePoints
    | Activate
    | Freeze
    | ExportPage
    | Investigate
    | StageModeration
    | SealModeration
    | Moderate,
    Field(discriminator='operation'),
]

_REQUEST = TypeAdapter[Request](Request.__value__)


def parse_request(payload: str) -> Request:
    if len(payload.encode()) > 1_048_576:
        raise ValueError('One management operation must fit the native 1 MiB bound.')

    return _REQUEST.validate_json(payload)


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
                raise ValueError('The numerical section must contain settlements.')

            settlement = Settlement.model_validate_json(canonical(numerical.value))

            if numerical.key != digest(settlement):
                raise ValueError('A settlement must bind its exact numerical value.')

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
                raise ValueError(
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
