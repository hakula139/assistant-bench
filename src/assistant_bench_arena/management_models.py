"""Private management request contracts and bounded native Service results."""

from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, TypeAdapter

from assistant_bench_arena.investigation_models import ReceiptFilter
from assistant_bench_arena.ledger_models import (
    Case,
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
from assistant_bench_arena.snapshots import AnalysisPolicy
from assistant_bench_arena.transport import OwnerInputError, Rejection


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
        raise OwnerInputError(
            'One management operation must fit the native 1 MiB bound.'
        )

    return _REQUEST.validate_json(payload)


class RpcError(Record):
    reason: Rejection | Literal['invalid_request', 'unavailable']
    detail: str | None = None
    """The owner's diagnosis for an invalid command or a bridge failure."""


class RpcFailure(Record):
    error: RpcError


class RpcSuccess[T](Record):
    result: T


type RpcResult[T] = RpcSuccess[T] | RpcFailure
