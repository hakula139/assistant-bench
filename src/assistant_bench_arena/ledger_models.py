"""Private ledger facts and control-plane inputs, independent of browser views."""

from typing import Annotated, Final, Literal, Self, get_args

from pydantic import (
    Field,
    JsonValue,
    NonNegativeInt,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from assistant_bench_arena.models import Category, Choice, Defects, FamilyName, Record
from assistant_bench_arena.snapshots import (
    AnalysisPolicy,
    CaseMetadata,
    EvaluationManifest,
    OutputMetadata,
    Presentation,
    PublicApproval,
)


MODERATION_CHUNK_SIZE: Final = 250
"""Maximum reviewed receipt targets applied by one moderation command."""


type SqlValue = StrictStr | StrictInt | StrictFloat | None
type Row = dict[str, JsonValue]
type Identifier = Annotated[str, Field(min_length=1, max_length=256)]
type Milliseconds = NonNegativeInt
type EventKind = Literal['activate', 'accept', 'withdraw', 'exclude', 'restore']


class Statement(Record):
    sql: str
    params: tuple[SqlValue, ...] = ()


class Case(Record):
    key: Identifier
    category: Category | None = None
    source: Literal['public', 'synthetic', 'private']
    instruction_digest: Identifier
    metadata: CaseMetadata


class System(Record):
    id: Identifier
    fingerprint: Identifier
    order: NonNegativeInt


class Output(Record):
    key: Identifier
    case_key: Identifier
    system_id: Identifier
    content_digest: Identifier
    verdict: Literal['pass', 'fail', 'ungraded']
    length: NonNegativeInt
    approved_public: bool = False
    """Explicit approval of the output, independent of its case's source."""

    metadata: OutputMetadata


class NumericalInput(Record):
    section: Identifier
    key: Identifier
    value: JsonValue


class Evaluation(Record):
    id: Identifier
    family: FamilyName
    policy_id: Identifier
    manifest: EvaluationManifest
    cases: NonNegativeInt
    systems: NonNegativeInt
    outputs: NonNegativeInt
    numerics: NonNegativeInt


class Publication(Record):
    id: Identifier
    evaluation_id: Identifier
    presentation: Presentation
    approval: PublicApproval | None
    outputs: NonNegativeInt


class RenderedOutput(Record):
    output_key: Identifier
    render: Literal['text', 'html', 'diff']
    content: str
    content_digest: Identifier
    approved_public: bool = False


class Point(Record):
    score: float | None
    component: str | None

    @model_validator(mode='after')
    def connected(self) -> Self:
        if (self.score is None) != (self.component is None):
            raise ValueError(
                'A point and its component must both be present or absent.'
            )

        return self


class PointSnapshot(Record):
    id: Identifier
    evaluation_id: Identifier
    policy_id: Identifier
    source_export_id: Identifier | None = None
    source_export_digest: Identifier | None = None
    weighted_evidence_digest: Identifier | None = None
    points: dict[str, Point]

    @model_validator(mode='after')
    def provenance(self) -> Self:
        supplied = (
            self.source_export_id is not None,
            self.source_export_digest is not None,
            self.weighted_evidence_digest is not None,
        )

        if any(supplied) and not all(supplied):
            raise ValueError('Fitted points require their complete source provenance.')

        return self


class Actor(Record):
    id: Identifier
    created_at: Milliseconds
    rate_subject: Identifier | None = None


class Session(Record):
    id: Identifier
    actor_id: Identifier
    family: FamilyName
    created_at: Milliseconds


class Context(Record):
    command_id: Identifier
    principal: Identifier
    now: Milliseconds
    """One trusted UTC clock reading for the complete command."""


class Quota(Record):
    operation: Literal['admit', 'renew', 'issue', 'accept']
    scope: Literal['actor', 'exact', 'prefix', 'deployment']
    window_ms: Annotated[int, Field(strict=True, gt=0)]
    limit: Annotated[int, Field(strict=True, gt=0)]


class BootstrapConfiguration(Record):
    namespace: Identifier
    quotas: tuple[Quota, ...]
    attribution_ms: Annotated[int, Field(strict=True, gt=0, le=5_184_000_000)]
    """Live keyed association lifetime, at most 60 days."""

    @field_validator('quotas')
    @classmethod
    def ordered_quotas(cls, quotas: tuple[Quota, ...]) -> tuple[Quota, ...]:
        return tuple(
            sorted(quotas, key=lambda row: (row.operation, row.scope, row.window_ms))
        )

    @model_validator(mode='after')
    def profile(self) -> Self:
        keys = {
            (quota.operation, quota.scope, quota.window_ms) for quota in self.quotas
        }

        if len(keys) != len(self.quotas):
            raise ValueError('Quota policies must have distinct operation windows.')

        return self


class DeploymentStatus(BootstrapConfiguration):
    schema_id: Literal['arena-v1']


class Attribution(Record):
    version: Identifier
    kind: Literal['exact', 'prefix']
    tag: Identifier
    expires_at: Milliseconds


class NetworkTag(Record):
    scope: Literal['exact', 'prefix']
    window_ms: Annotated[int, Field(strict=True, gt=0)]
    tag: Identifier


class Network(Record):
    tags: tuple[NetworkTag, ...] = ()
    """Purpose-separated tags for each applicable UTC quota window."""


class Admission(Record):
    operation: Literal['new', 'renew']
    context_digest: Identifier
    """Verified bootstrap context or the original signed renewal credential."""

    proof_digest: Identifier
    """Digest of the successfully verified challenge, with no proof plaintext."""

    original_credential: Identifier | None = None
    actor_id: Identifier
    credential_id: Identifier
    rate_subject: Identifier
    expires_at: Milliseconds
    challenge_until: Milliseconds
    network: Network = Network()

    @model_validator(mode='after')
    def renewal(self) -> Self:
        if (self.operation == 'renew') != (self.original_credential is not None):
            raise ValueError('Renewal requires the original credential identity.')

        return self


class AdmissionResult(Record):
    command_id: str
    request_hash: str
    operation: Literal['new', 'renew']
    context_digest: str
    proof_digest: str
    original_credential: str | None
    actor_id: str
    credential_id: str
    rate_subject: str
    issued_at: Milliseconds
    expires_at: Milliseconds
    challenge_until: Milliseconds


class Credential(Record):
    id: str
    actor_id: str
    rate_subject: str
    issued_at: Milliseconds
    expires_at: Milliseconds
    challenge_until: Milliseconds
    revoked_at: Milliseconds | None
    renewal_command: str | None


class Issue(Record):
    lease_id: Identifier
    session_id: Identifier
    session_version: NonNegativeInt
    claim_id: Identifier
    claim_version: NonNegativeInt
    case_key: Identifier
    a_output: Identifier
    b_output: Identifier
    expires_at: Milliseconds
    repeat_of: NonNegativeInt | None = None
    network: Network = Network()


class VoteMeaning(Record):
    lease_id: Identifier
    choice: Choice
    defects: Defects = Defects()


class Accept(VoteMeaning):
    acknowledgment: Identifier
    network: Network = Network()
    attribution: Annotated[tuple[Attribution, ...], Field(max_length=2)] = ()


class Event(Record):
    seq: NonNegativeInt
    command_id: str
    kind: EventKind
    family: FamilyName
    principal: str
    request_hash: str
    created_at: Milliseconds
    data: dict[str, JsonValue]


class EventTarget(Record):
    event_seq: NonNegativeInt
    ordinal: NonNegativeInt
    receipt_seq: NonNegativeInt | None
    claim_id: str | None
    expected_version: NonNegativeInt | None
    transition: str
    reference_seq: NonNegativeInt | None
    lease_id: str | None


class Lease(Record):
    id: str
    command_id: str
    request_hash: str

    actor_id: str
    family: FamilyName
    session_id: str

    claim_id: str
    claim_version: NonNegativeInt
    """Claim revision after issuance, required unchanged at acceptance."""

    evaluation_id: str
    publication_id: str
    point_id: str

    case_key: str
    a_output: str
    b_output: str
    a_system: str
    b_system: str
    a_fingerprint: str
    b_fingerprint: str

    issued_at: Milliseconds
    expires_at: Milliseconds
    repeat_of: NonNegativeInt | None
    """Original ordinary receipt sequence for a swapped delayed repeat."""

    status: Literal['pending', 'accepted', 'revoked']
    receipt_seq: NonNegativeInt | None


class Receipt(Record):
    seq: NonNegativeInt
    acknowledgment: str

    actor_id: str
    session_id: str
    family: FamilyName
    claim_id: str
    lease_id: str

    evaluation_id: str
    publication_id: str
    point_id: str
    claim_version: NonNegativeInt
    """Revision before acceptance advances the claim's selection."""

    case_key: str
    a_output: str
    b_output: str
    a_system: str
    b_system: str
    a_fingerprint: str
    b_fingerprint: str

    choice: Choice
    defects: Defects
    repeat_of: NonNegativeInt | None
    """Original ordinary receipt sequence. Repeats carry zero fitting mass."""

    recorded_at: Milliseconds
    """Server acceptance time."""

    duration_ms: NonNegativeInt


class Target(Record):
    receipt_seq: Annotated[int, Field(strict=True, gt=0)]
    claim_version: NonNegativeInt
    exclusion_seq: NonNegativeInt | None = None


class Moderation(Record):
    id: Identifier
    family: FamilyName
    principal: Identifier
    action: Literal['exclude', 'restore']
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    created_at: Milliseconds
    targets: Annotated[tuple[Target, ...], Field(min_length=1)]


type ExportSection = Literal[
    'evaluation',
    'publication',
    'points',
    'policy',
    'cases',
    'systems',
    'outputs',
    'numerics',
    'receipts',
    'events',
    'targets',
    'actors',
    'sessions',
]


class Export(Record):
    id: str
    family: FamilyName
    schema_version: Literal[1]
    namespace: str
    created_at: Milliseconds

    event_cutoff: NonNegativeInt
    """Inclusive global event sequence captured for this family's history."""

    receipt_cutoff: NonNegativeInt
    """Inclusive global receipt sequence captured for this family's votes."""

    family_revision: NonNegativeInt
    """Captured control revision, including presentation and point changes."""

    analysis_sequence: NonNegativeInt
    """Captured numerical-input revision used to assess analysis freshness."""

    evaluation_id: str
    publication_id: str
    point_id: str
    policy_id: str
    policy: AnalysisPolicy

    sections: dict[ExportSection, NonNegativeInt]
    """Authoritative row counts derived from the immutable frozen views."""

    @model_validator(mode='after')
    def complete_sections(self) -> Self:
        if set(self.sections) != set(get_args(ExportSection.__value__)):
            raise ValueError('An export manifest must name every section exactly once.')

        return self


class Page(Record):
    rows: list[Row]
    cursor: str | None


class Cursor(Record):
    export_id: Identifier
    section: ExportSection
    key: str
