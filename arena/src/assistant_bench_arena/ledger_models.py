"""Private ledger facts and control-plane inputs, independent of browser views."""

from typing import Annotated, Literal, Self, get_args

from pydantic import (
    Field,
    JsonValue,
    NonNegativeInt,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from assistant_bench_arena.models import Choice, Defects, Record
from assistant_bench_arena.serialization import digest
from assistant_bench_arena.snapshots import (
    AnalysisPolicy,
    CaseMetadata,
    EvaluationManifest,
    OutputMetadata,
    Presentation,
    PublicApproval,
)


type SqlValue = StrictStr | StrictInt | StrictFloat | None
type Row = dict[str, JsonValue]
type Identifier = Annotated[str, Field(min_length=1, max_length=256)]
type Milliseconds = NonNegativeInt
type Mode = Literal['local', 'public']
type EventKind = Literal[
    'activate', 'accept', 'withdraw', 'exclude', 'restore', 'attribute', 'import'
]


class Statement(Record):
    sql: str
    params: tuple[SqlValue, ...] = ()


class DeploymentStatus(Record):
    namespace: str
    mode: Mode
    local_key_id: str | None
    schema_version: Literal[1]


class Case(Record):
    key: Identifier
    category: str | None = None
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
    family: Identifier
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
    owner_namespace: Identifier | None = None
    """Explicit registration of the importing owner's source namespace."""

    rate_subject: Identifier | None = None


class Session(Record):
    id: Identifier
    actor_id: Identifier
    family: Identifier
    created_at: Milliseconds
    import_source: Identifier | None = None
    original_id: Identifier | None = None


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
    mode: Mode
    quotas: tuple[Quota, ...]
    attribution_ms: Annotated[int, Field(strict=True, ge=0, le=5_184_000_000)] | None
    """Live keyed association lifetime, at most 60 days."""

    local_key_id: Identifier | None

    @model_validator(mode='after')
    def profile(self) -> Self:
        if self.mode == 'public' and not self.attribution_ms:
            raise ValueError(
                'A public profile requires an explicit attribution lifetime.'
            )

        if (self.mode == 'local' and self.local_key_id is None) or (
            self.mode == 'public' and self.local_key_id is not None
        ):
            raise ValueError('Only a local profile requires a keyring identity.')

        keys = {
            (quota.operation, quota.scope, quota.window_ms) for quota in self.quotas
        }

        if len(keys) != len(self.quotas):
            raise ValueError('Quota policies must have distinct operation windows.')

        return self


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
    family: str
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
    target_claim: str | None
    target_actor: str | None
    source_row: NonNegativeInt | None


class Lease(Record):
    id: str
    command_id: str
    request_hash: str
    actor_id: str
    family: str
    session_id: str
    claim_id: str
    claim_version: NonNegativeInt
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
    status: Literal['pending', 'accepted', 'revoked']
    receipt_seq: NonNegativeInt | None


class ObservationFacts(Record):
    seq: NonNegativeInt
    acknowledgment: str
    actor_id: str
    session_id: str
    family: str
    claim_id: str
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
    recorded_at: Milliseconds
    """Server acceptance time for served votes or the original legacy timestamp."""


class Receipt(ObservationFacts):
    origin: Literal['served'] = 'served'
    lease_id: str
    evaluation_id: str
    publication_id: str
    point_id: str
    claim_version: NonNegativeInt
    duration_ms: NonNegativeInt


class LegacyReceipt(ObservationFacts):
    origin: Literal['legacy'] = 'legacy'
    import_source: str
    original_id: str
    import_row_seq: NonNegativeInt
    proof_evaluation_id: str
    """Sealed inventory that proved the binding, separate from served provenance."""

    legacy_duration_sec: Annotated[float, Field(ge=0)]


type Observation = Annotated[Receipt | LegacyReceipt, Field(discriminator='origin')]


class Target(Record):
    receipt_seq: Annotated[int, Field(strict=True, gt=0)]
    claim_version: NonNegativeInt
    exclusion_seq: NonNegativeInt | None = None


class ReceiptAnnotation(Record):
    kind: Literal['receipt']
    receipt_seq: Annotated[int, Field(strict=True, gt=0)]


class ImportAnnotation(Record):
    kind: Literal['import']
    source_id: Identifier
    original_id: Identifier


type AnnotationTarget = Annotated[
    ReceiptAnnotation | ImportAnnotation, Field(discriminator='kind')
]


class AttributionTarget(Record):
    claim_id: Identifier
    version: NonNegativeInt
    canonical_actor: Identifier
    kind: Literal['ordinary', 'repeat']
    comparison_key: str
    selected_receipt: NonNegativeInt | None
    winner_id: Identifier


class AttributionPlan(Record):
    id: Identifier
    family: Identifier
    revision: NonNegativeInt
    actor_id: Identifier
    owner_id: Identifier
    targets: tuple[AttributionTarget, ...]


class AttributionClaim(Record):
    id: str
    canonical_actor: str
    kind: Literal['ordinary', 'repeat']
    comparison_key: str
    version: NonNegativeInt
    selected_receipt: NonNegativeInt | None
    recorded_at: Milliseconds | None


class Moderation(Record):
    id: Identifier
    family: Identifier
    principal: Identifier
    action: Literal['exclude', 'restore']
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    created_at: Milliseconds
    targets: Annotated[tuple[Target, ...], Field(min_length=1)]


class LegacySession(Record):
    id: Identifier
    family: Identifier
    started_at: str


class LegacyVote(Record):
    id: StrictInt
    session: Identifier
    case_id: Identifier
    a_system: Identifier
    a_fingerprint: Identifier | None
    a_job: Identifier
    a_trial: Identifier
    b_system: Identifier
    b_fingerprint: Identifier | None
    b_job: Identifier
    b_trial: Identifier
    choice: Choice
    a_defects: str
    b_defects: str
    duration_sec: float
    repeat_of: StrictInt | None
    recorded_at: str


class LegacyWithdrawal(Record):
    vote: StrictInt
    undone_at: str


class ImportSummary(Record):
    source_id: str
    row_kind: Literal['session', 'receipt', 'withdrawal']
    original_id: str
    seq: NonNegativeInt
    row_hash: str
    initial_disposition: Literal['unresolved', 'incompatible']
    repeat_of: StrictInt | None


class ImportSource(Record):
    id: Identifier
    family: Identifier
    owner_id: Identifier
    source_schema: Identifier
    file_digest: Identifier
    sessions: NonNegativeInt
    receipts: NonNegativeInt
    withdrawals: NonNegativeInt


class ImportRow(Record):
    kind: Literal['session', 'receipt', 'withdrawal']
    original_id: Identifier
    source_hash: Identifier
    raw: LegacySession | LegacyVote | LegacyWithdrawal
    """The v2 source fields, with notes in the separate erasable channel."""

    initial_disposition: Literal['unresolved', 'incompatible'] = 'unresolved'

    @model_validator(mode='after')
    def source_row(self) -> Self:
        expected = {
            'session': LegacySession,
            'receipt': LegacyVote,
            'withdrawal': LegacyWithdrawal,
        }[self.kind]

        if not isinstance(self.raw, expected):
            raise ValueError('An import row must match its declared source table.')

        original = (
            self.raw.vote if isinstance(self.raw, LegacyWithdrawal) else self.raw.id
        )

        if self.original_id != str(original) or self.source_hash != digest(self.raw):
            raise ValueError(
                'An import row must retain its original identity and hash.'
            )

        return self


class ImportSession(Record):
    original_id: Identifier
    session_id: Identifier
    created_at: Milliseconds


class ImportObservation(Record):
    raw_seq: Annotated[int, Field(strict=True, gt=0)]
    raw_hash: Identifier
    command_id: Identifier
    acknowledgment: Identifier
    claim_id: Identifier
    proof_evaluation_id: Identifier
    case_key: Identifier
    a_output: Identifier
    b_output: Identifier
    a_source_digest: Identifier
    b_source_digest: Identifier
    recorded_at: Milliseconds


class ImportSelection(Record):
    claim_id: Identifier
    expected_version: NonNegativeInt
    expected_receipt: NonNegativeInt | None
    selected_acknowledgment: Identifier | None
    """An explicit winner, including preservation of an existing served choice."""


class ImportDisposition(Record):
    raw_seq: Annotated[int, Field(strict=True, gt=0)]
    state: Literal['linked', 'unresolved', 'incompatible']
    reason: Annotated[
        str, Field(min_length=1, max_length=64, pattern='^[a-z][a-z0-9_]*$')
    ]


class ImportPlan(Record):
    id: Identifier
    source_id: Identifier
    revision: NonNegativeInt
    initial: bool
    sessions: tuple[ImportSession, ...]
    observations: Annotated[tuple[ImportObservation, ...], Field(max_length=250)]
    selections: Annotated[tuple[ImportSelection, ...], Field(max_length=250)]
    dispositions: tuple[ImportDisposition, ...]

    @model_validator(mode='after')
    def unique_rows(self) -> Self:
        for rows, keys in (
            (self.sessions, [row.original_id for row in self.sessions]),
            (self.observations, [row.raw_seq for row in self.observations]),
            (self.selections, [row.claim_id for row in self.selections]),
            (self.dispositions, [row.raw_seq for row in self.dispositions]),
        ):
            if len(rows) != len(set(keys)):
                raise ValueError('An import plan must name each source identity once.')

        linked = {row.raw_seq for row in self.dispositions if row.state == 'linked'}

        if {row.claim_id for row in self.selections} != {
            row.claim_id for row in self.observations
        }:
            raise ValueError('An import plan must review exactly its affected claims.')

        if len({row.command_id for row in self.observations}) != len(self.observations):
            raise ValueError(
                'Imported observations require distinct command identities.'
            )

        if len({row.acknowledgment for row in self.observations}) != len(
            self.observations
        ):
            raise ValueError('Imported observations require distinct acknowledgments.')

        if linked != {row.raw_seq for row in self.observations}:
            raise ValueError(
                'Every linked disposition requires one proved observation.'
            )

        return self


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
    'import_sources',
    'import_rows',
]


class Export(Record):
    id: str
    family: str
    schema_version: Literal[1]
    namespace: str
    created_at: Milliseconds
    event_cutoff: NonNegativeInt
    receipt_cutoff: NonNegativeInt
    import_cutoff: NonNegativeInt
    import_source_cutoff: NonNegativeInt
    family_revision: NonNegativeInt
    analysis_sequence: NonNegativeInt
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
