"""Private ledger facts and control-plane inputs, independent of browser views."""

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from assistant_bench_arena.models import Choice, Defects


type SqlValue = StrictStr | StrictInt | StrictFloat | None
type Row = dict[str, JsonValue]
type Identifier = Annotated[str, Field(min_length=1, max_length=256)]
type Milliseconds = NonNegativeInt
type Mode = Literal['local', 'public']
type EventKind = Literal[
    'activate', 'accept', 'withdraw', 'exclude', 'restore', 'attribute', 'import'
]


class Record(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        use_attribute_docstrings=True,
    )


class Statement(Record):
    sql: str
    params: tuple[SqlValue, ...] = ()


class Case(Record):
    key: Identifier
    category: str | None = None
    source: Literal['public', 'synthetic', 'private']
    instruction_digest: Identifier
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class System(Record):
    id: Identifier
    fingerprint: Identifier
    order: NonNegativeInt
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Output(Record):
    key: Identifier
    case_key: Identifier
    system_id: Identifier
    content_digest: Identifier
    verdict: Literal['pass', 'fail', 'ungraded']
    length: NonNegativeInt
    approved_public: bool = False
    """Explicit approval of the output, independent of its case's source."""

    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class NumericalInput(Record):
    section: Identifier
    key: Identifier
    value: JsonValue


class Evaluation(Record):
    id: Identifier
    family: Identifier
    policy_id: Identifier
    manifest: dict[str, JsonValue]
    cases: NonNegativeInt
    systems: NonNegativeInt
    outputs: NonNegativeInt
    numerics: NonNegativeInt


class Publication(Record):
    id: Identifier
    evaluation_id: Identifier
    presentation: dict[str, JsonValue]
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
    points: dict[str, Point]


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


class Accept(Record):
    lease_id: Identifier
    acknowledgment: Identifier
    choice: Choice
    defects: Defects = Defects()
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


class Receipt(Record):
    seq: NonNegativeInt
    acknowledgment: str
    lease_id: str
    actor_id: str
    session_id: str
    family: str
    evaluation_id: str
    publication_id: str
    point_id: str
    claim_id: str
    claim_version: NonNegativeInt
    case_key: str
    a_output: str
    b_output: str
    a_system: str
    b_system: str
    a_fingerprint: str
    b_fingerprint: str
    choice: Choice
    defects: Defects
    duration_ms: NonNegativeInt
    repeat_of: NonNegativeInt | None
    accepted_at: Milliseconds
    import_source: str | None = None
    original_id: str | None = None


class Target(Record):
    receipt_seq: Annotated[int, Field(strict=True, gt=0)]
    claim_version: NonNegativeInt
    exclusion_seq: NonNegativeInt | None = None


class Moderation(Record):
    id: Identifier
    family: Identifier
    principal: Identifier
    action: Literal['exclude', 'restore']
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    created_at: Milliseconds
    targets: Annotated[tuple[Target, ...], Field(min_length=1)]


class ImportSource(Record):
    id: Identifier
    owner_id: Identifier
    source_schema: Identifier
    file_digest: Identifier


class ImportRow(Record):
    kind: Literal['session', 'receipt', 'withdrawal', 'repeat', 'annotation']
    original_id: Identifier
    source_hash: Identifier
    raw: dict[str, JsonValue]
    """Unmodified provenance retained until an exact output identity is proved."""

    linkage: Literal['unresolved', 'incompatible', 'linked'] = 'unresolved'
    target_id: Identifier | None = None

    @model_validator(mode='after')
    def linked(self) -> Self:
        if (self.linkage == 'linked') != (self.target_id is not None):
            raise ValueError('Only a linked source row names a target identity.')

        return self


class Export(Record):
    id: str
    family: str
    schema_version: Literal[1]
    namespace: str
    created_at: Milliseconds
    event_cutoff: NonNegativeInt
    receipt_cutoff: NonNegativeInt
    import_cutoff: NonNegativeInt
    family_revision: NonNegativeInt
    analysis_sequence: NonNegativeInt
    evaluation_id: str
    publication_id: str
    point_id: str
    policy_id: str
    policy: dict[str, JsonValue]


class Page(Record):
    rows: list[Row]
    cursor: str | None


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


class Cursor(Record):
    export_id: Identifier
    section: ExportSection
    key: str


def canonical(value: BaseModel | JsonValue) -> str:
    """Stable JSON for stored facts and semantic command identity."""
    data = value.model_dump(mode='json') if isinstance(value, BaseModel) else value
    return json.dumps(
        data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False
    )


def digest(value: BaseModel | JsonValue) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
