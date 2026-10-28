"""Frozen numerical policy and allowlisted publication metadata."""

from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from assistant_bench_arena.models import Category, Defect, FamilyName, Record


class VotesMethod(Record):
    l2_penalty: Annotated[float, Field(gt=0)]
    """Penalty on system logits, excluding the sided intercept."""

    points_per_logit: Annotated[float, Field(gt=0)]
    anchor_score: float
    actor_cap: Annotated[int, Field(strict=True, gt=0)]
    """Each of an actor's `n` eligible choices weighs `min(1, actor_cap / n)`."""


class VotesRules(Record):
    preliminary_decisive_votes: Annotated[int, Field(strict=True, gt=0)]
    """Raw decisive votes below which a standing is preliminary."""


class AnalysisPolicy(Record):
    method: VotesMethod
    rules: VotesRules


class FamilyDefinition(Record):
    name: FamilyName
    reference: Annotated[str, Field(min_length=1)]
    systems: tuple[str, ...]
    categories: tuple[Category, ...]
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
    family: FamilyName
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
    family: FamilyName
    aspect: str | None
    display_name: Annotated[str, Field(min_length=1)]
    render: Literal['text', 'html', 'diff']
    systems: dict[str, Annotated[str, Field(min_length=1)]]
    cases: dict[str, PresentedCase]
