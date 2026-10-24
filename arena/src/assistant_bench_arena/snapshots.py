"""Frozen numerical policy and allowlisted publication metadata."""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from assistant_bench_arena.models import Defect


class Record(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        use_attribute_docstrings=True,
    )


class UnitHumanWeights(Record):
    name: Literal['unit']
    version: Literal[1]
    repeat_handling: Literal['included']


class CappedHumanWeights(Record):
    name: Literal['actor-proportional-cap']
    version: Literal[1]
    cap: Annotated[int, Field(strict=True, gt=0)]
    scope: Literal['family-evaluation']
    repeat_handling: Literal['excluded']


type HumanWeights = Annotated[
    UnitHumanWeights | CappedHumanWeights, Field(discriminator='name')
]


class SettledWeights(Record):
    name: Literal['sample-pair-share']
    version: Literal[1]


class VotesMethod(Record):
    name: Literal['bradley-terry']
    version: Literal[1]

    l2_penalty: Annotated[float, Field(gt=0)]
    """Penalty on system logits, excluding the sided intercept."""

    points_per_logit: Annotated[float, Field(gt=0)]
    anchor_score: float
    human_weights: HumanWeights
    settled_weights: SettledWeights


class VotesRules(Record):
    preliminary_decisive_votes: Annotated[int, Field(strict=True, gt=0)]
    """Raw coverage threshold, independent of inference availability."""

    preliminary_decisive_vote_unit: Literal['raw-votes']
    rater_agreement_floor: Annotated[float, Field(ge=0, le=1)]


class UnavailableInference(Record):
    status: Literal['unavailable']
    target: Literal['new-cases-and-voters']
    reason: Literal['population-method-not-validated']


class AnalysisPolicy(Record):
    method: VotesMethod
    rules: VotesRules
    inference: UnavailableInference


class UnrecordedProvenance(Record):
    status: Literal['unrecorded']


class SnapshotProvenance(Record):
    schema_version: Literal[1]
    export_id: Annotated[str, Field(min_length=1)]
    export_digest: Annotated[str, Field(min_length=1)]
    namespace: Annotated[str, Field(min_length=1)]
    captured_at: AwareDatetime
    event_cutoff: Annotated[int, Field(strict=True, ge=0)]
    receipt_cutoff: Annotated[int, Field(strict=True, ge=0)]
    import_cutoff: Annotated[int, Field(strict=True, ge=0)]
    import_source_cutoff: Annotated[int, Field(strict=True, ge=0)]
    family_revision: Annotated[int, Field(strict=True, ge=0)]
    analysis_sequence: Annotated[int, Field(strict=True, ge=0)]
    evaluation_id: Annotated[str, Field(min_length=1)]
    publication_id: Annotated[str, Field(min_length=1)]
    policy_id: Annotated[str, Field(min_length=1)]
    sampling_point_snapshot_id: Annotated[str, Field(min_length=1)]


class LedgerProvenance(SnapshotProvenance):
    status: Literal['ledger']
    weighted_evidence_digest: Annotated[str, Field(min_length=1)]
    fitted_point_snapshot_id: Annotated[str, Field(min_length=1)]


type Provenance = Annotated[
    UnrecordedProvenance | LedgerProvenance, Field(discriminator='status')
]


class FamilyDefinition(Record):
    name: Annotated[str, Field(min_length=1)]
    reference: Annotated[str, Field(min_length=1)]
    systems: tuple[str, ...]
    categories: tuple[str, ...]
    defects: tuple[Defect, ...]

    @model_validator(mode='after')
    def unique_members(self) -> Self:
        if any(
            len(set(values)) != len(values)
            for values in (self.systems, self.categories, self.defects)
        ):
            raise ValueError('Family systems, categories and defects must be unique.')

        if self.reference not in self.systems:
            raise ValueError('The reference must belong to the frozen system order.')

        return self


class EvaluationManifest(Record):
    family: FamilyDefinition
    policy: AnalysisPolicy


class Settlement(Record):
    case_key: Annotated[str, Field(min_length=1)]
    a_output: Annotated[str, Field(min_length=1)]
    b_output: Annotated[str, Field(min_length=1)]
    score: float
    weight: Annotated[float, Field(gt=0)]

    @field_validator('score')
    @classmethod
    def valid_score(cls, value: float) -> float:
        if value not in (0.0, 0.5, 1.0):
            raise ValueError('A settlement score must be 0, 0.5 or 1.')

        return value


class CaseContentApproval(Record):
    case_key: Annotated[str, Field(min_length=1)]
    source: Literal['synthetic', 'public']
    attestation: Annotated[str, Field(min_length=1)]
    """Maintainer attestation covering source permission and license."""

    instruction_digest: Annotated[str, Field(min_length=1)]


class CaseApproval(CaseContentApproval):
    presentation_digest: Annotated[str, Field(min_length=1)]
    """Identity of every visible instruction, checklist and context byte."""


class OutputContentApproval(Record):
    output_key: Annotated[str, Field(min_length=1)]
    content_digest: Annotated[str, Field(min_length=1)]


class OutputApproval(OutputContentApproval):
    render_digest: Annotated[str, Field(min_length=1)]


class PublicApproval(Record):
    family: Annotated[str, Field(min_length=1)]
    cases: tuple[CaseApproval, ...]
    outputs: tuple[OutputApproval, ...]

    @model_validator(mode='after')
    def unique_items(self) -> Self:
        if len({case.case_key for case in self.cases}) != len(self.cases) or len(
            {output.output_key for output in self.outputs}
        ) != len(self.outputs):
            raise ValueError('An approval must name each case and output at most once.')

        return self


class CaseMetadata(Record):
    source_digest: Annotated[str, Field(min_length=1)]
    """Identity of the instruction and scoring-relevant source material."""

    approval: CaseContentApproval | None


class OutputMetadata(Record):
    source_digest: Annotated[str, Field(min_length=1)]
    """Opaque identity of the exact solution source, including regrade ancestry."""

    errored: bool
    reward: float | None
    cost_usd: Annotated[float, Field(ge=0)] | None
    latency_sec: Annotated[float, Field(ge=0)] | None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None
    approval: OutputContentApproval | None


class PresentedCase(Record):
    instruction: str
    checklist: tuple[str, ...]


class Presentation(Record):
    family: Annotated[str, Field(min_length=1)]
    aspect: str | None
    display_name: Annotated[str, Field(min_length=1)]
    render: Literal['text', 'html', 'diff']
    systems: dict[str, Annotated[str, Field(min_length=1)]]
    cases: dict[str, PresentedCase]
