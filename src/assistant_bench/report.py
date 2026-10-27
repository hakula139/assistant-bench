"""The report schema and inputs, the fixed votes method, and the writer of rounds.

A report holds aggregates under public system names and nothing else: no case,
instruction, output text, route, or request ID.
"""

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from assistant_bench.families import (
    Choice,
    Defect,
    Family,
    FamilyName,
    TaskTestsSettings,
    load_cases,
)
from assistant_bench.outputs import Output, read_outputs
from assistant_bench.paths import REPORTS_DIR, write_atomically
from assistant_bench.scoring import ranking, weighting


SWAPPED: dict[Choice, Choice] = {
    'a': 'b',
    'b': 'a',
    'tie': 'tie',
    'both_bad': 'both_bad',
}

# ── Shared Schema ──


class _Model(BaseModel):
    model_config = ConfigDict(
        extra='forbid', frozen=True, allow_inf_nan=False, use_attribute_docstrings=True
    )


class PublicSystem(_Model):
    display_name: str
    """Public label frozen for this round, including retired systems."""

    fingerprint: str | None
    """Configuration provenance, unknown when null."""

    @field_validator('display_name')
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('A public display name must contain text.')
        return value


class Distribution(_Model):
    p10: float
    p50: float
    p90: float


# ── Votes Method ──


class UnitHumanWeights(_Model):
    name: Literal['unit']
    version: Literal[1]
    repeat_handling: Literal['included']


class CappedHumanWeights(_Model):
    name: Literal['actor-proportional-cap']
    version: Literal[1]
    cap: Annotated[int, Field(strict=True, gt=0)]
    scope: Literal['family-evaluation']
    repeat_handling: Literal['excluded']


type HumanWeights = Annotated[
    UnitHumanWeights | CappedHumanWeights, Field(discriminator='name')
]


class SettledWeights(_Model):
    name: Literal['sample-pair-share']
    version: Literal[1]


class VotesMethod(_Model):
    name: Literal['bradley-terry']
    version: Literal[1]

    l2_penalty: Annotated[float, Field(gt=0)]
    """Penalty on system logits, excluding the sided intercept."""

    points_per_logit: Annotated[float, Field(gt=0)]
    anchor_score: float
    human_weights: HumanWeights
    settled_weights: SettledWeights


class VotesRules(_Model):
    preliminary_decisive_votes: Annotated[int, Field(strict=True, gt=0)]
    """Raw coverage threshold, independent of inference availability."""

    preliminary_decisive_vote_unit: Literal['raw-votes']
    rater_agreement_floor: Annotated[float, Field(ge=0, le=1)]


class UnavailableInference(_Model):
    status: Literal['unavailable']
    target: Literal['new-cases-and-voters']
    reason: Literal['population-method-not-validated']


METHOD = VotesMethod(
    name='bradley-terry',
    version=1,
    l2_penalty=ranking.L2_PENALTY,
    points_per_logit=ranking.ELO_PER_LOGIT,
    anchor_score=ranking.ANCHOR,
    human_weights=CappedHumanWeights(
        name='actor-proportional-cap',
        version=1,
        cap=weighting.CAP,
        scope='family-evaluation',
        repeat_handling='excluded',
    ),
    settled_weights=SettledWeights(name='sample-pair-share', version=1),
)

RULES = VotesRules(
    preliminary_decisive_votes=40,
    preliminary_decisive_vote_unit='raw-votes',
    rater_agreement_floor=0.7,
)

INFERENCE = UnavailableInference(
    status='unavailable',
    target='new-cases-and-voters',
    reason='population-method-not-validated',
)


# ── Votes Schema ──


class UnrecordedProvenance(_Model):
    status: Literal['unrecorded']


class LedgerProvenance(_Model):
    schema_version: Literal[1]
    export_id: Annotated[str, Field(min_length=1)]
    export_digest: Annotated[str, Field(min_length=1)]
    namespace: Annotated[str, Field(min_length=1)]
    captured_at: AwareDatetime
    event_cutoff: Annotated[int, Field(strict=True, ge=0)]
    receipt_cutoff: Annotated[int, Field(strict=True, ge=0)]
    family_revision: Annotated[int, Field(strict=True, ge=0)]
    analysis_sequence: Annotated[int, Field(strict=True, ge=0)]
    evaluation_id: Annotated[str, Field(min_length=1)]
    publication_id: Annotated[str, Field(min_length=1)]
    policy_id: Annotated[str, Field(min_length=1)]
    sampling_point_snapshot_id: Annotated[str, Field(min_length=1)]
    status: Literal['ledger']
    weighted_evidence_digest: Annotated[str, Field(min_length=1)]
    fitted_point_snapshot_id: Annotated[str, Field(min_length=1)]


type Provenance = Annotated[
    UnrecordedProvenance | LedgerProvenance, Field(discriminator='status')
]


class Standing(_Model):
    system: str
    score: float
    """Point estimate on the round's recorded score scale."""

    interval: None
    rank: int
    """Competition rank of the point score."""
    best_rank: None
    worst_rank: None

    votes: Annotated[int, Field(ge=0)]
    effective_votes: Annotated[float, Field(ge=0)]
    """Eligible human row weight, excluding settled comparisons."""
    decisive_votes: Annotated[int, Field(ge=0)]
    both_bad_rate: float | None

    outputs: int
    """Outputs that finished without an error."""
    gate_failure_rate: float | None
    """Failure share among outputs with a verdict, including errored agents."""

    mean_cost_usd: float | None
    p50_latency_sec: float | None
    output_tokens: Distribution | None
    latency_sec: Distribution | None

    defects: dict[Defect, int]
    """Raw tagged-vote counts in the family's defect order."""


class HeadToHead(_Model):
    """Human choices between two systems, with half credit for either tie."""

    system: str
    opponent: str
    votes: Annotated[int, Field(ge=0)]
    effective_votes: Annotated[float, Field(ge=0)]
    wins: int
    losses: int
    ties: int
    both_bad: int
    win_rate: float


class CategoryStanding(_Model):
    system: str
    score: float
    interval: None
    votes: Annotated[int, Field(ge=0)]
    effective_votes: Annotated[float, Field(ge=0)]
    decisive_votes: Annotated[int, Field(ge=0)]


class CategoryBoard(_Model):
    """A category point fit in the overall anchor's connected component."""

    category: str
    votes: Annotated[int, Field(ge=0)]
    effective_votes: Annotated[float, Field(ge=0)]
    standings: list[CategoryStanding]
    unranked: list[str]


class RaterAgreement(_Model):
    """Descriptive agreement of side-swapped repeats with their originals."""

    repeats: int
    agreement: float | None
    below_floor: bool


class VotesResults(_Model):
    """Point estimates and descriptive diagnostics on eligible comparisons."""

    scorer: Literal['votes']
    method: VotesMethod
    rules: VotesRules
    inference: UnavailableInference
    provenance: Provenance = Field(
        default=UnrecordedProvenance(status='unrecorded'),
        exclude_if=lambda value: isinstance(value, UnrecordedProvenance),
    )

    reference: str
    """Configured reference, whether or not comparisons connect it."""
    anchor: str
    """Ranked system fixed at the recorded anchor score."""

    votes: Annotated[int, Field(ge=0)]
    """Eligible human choices across every component."""
    effective_votes: Annotated[float, Field(ge=0)]
    """Eligible human row weight across every component, excluding settlements."""
    dropped_votes: int
    """Accepted choices excluded from the fit, including repeats and moderation."""
    sessions: int
    """Sessions containing at least one eligible human choice."""

    standings: list[Standing]
    unranked: list[str]
    """Entrants in the frozen evaluation outside the comparison component."""
    head_to_head: list[HeadToHead]
    categories: list[CategoryBoard]

    position: float | None
    """Left-pane advantage in score points, null without ranked human rows."""
    length: float | None
    """Score points per length-difference standard deviation, if identifiable."""
    separability: None
    split_half_tau: float | None
    """Order agreement between alternating eligible sessions, when measurable."""
    rater_agreement: RaterAgreement
    decision: None

    @model_validator(mode='after')
    def validate_rules(self) -> VotesResults:
        aggregates = [
            (self.votes, self.effective_votes),
            *((row.votes, row.effective_votes) for row in self.standings),
            *((row.votes, row.effective_votes) for row in self.head_to_head),
            *((board.votes, board.effective_votes) for board in self.categories),
            *(
                (row.votes, row.effective_votes)
                for board in self.categories
                for row in board.standings
            ),
        ]
        if isinstance(self.method.human_weights, UnitHumanWeights) and any(
            raw != effective for raw, effective in aggregates
        ):
            raise ValueError(
                'Unit human weights require effective votes to equal raw votes.'
            )

        if any(effective > raw for raw, effective in aggregates):
            raise ValueError('Effective votes cannot exceed raw votes.')

        if isinstance(self.method.human_weights, CappedHumanWeights) and not isinstance(
            self.provenance, LedgerProvenance
        ):
            raise ValueError('Capped human weights require sealed ledger provenance.')

        if isinstance(self.method.human_weights, CappedHumanWeights):
            self._validate_capped_aggregates()

        agreement = self.rater_agreement.agreement
        below = agreement is not None and agreement < self.rules.rater_agreement_floor
        if self.rater_agreement.below_floor != below:
            raise ValueError('Rater agreement must use the recorded agreement floor.')

        return self

    def _validate_capped_aggregates(self) -> None:
        def close(left: float, right: float) -> bool:
            return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)

        def bounded(raw: int, effective: float, total: int, mass: float) -> bool:
            return raw <= total and (effective <= mass or close(effective, mass))

        children: list[Standing | HeadToHead | CategoryBoard] = [
            *self.standings,
            *self.head_to_head,
            *self.categories,
        ]
        if any(
            not bounded(
                row.votes, row.effective_votes, self.votes, self.effective_votes
            )
            for row in children
        ):
            raise ValueError('Subset counts cannot exceed complete family evidence.')

        categories = [board.category for board in self.categories]
        if len(categories) != len(set(categories)) or not bounded(
            sum(board.votes for board in self.categories),
            sum(board.effective_votes for board in self.categories),
            self.votes,
            self.effective_votes,
        ):
            raise ValueError('Category boards must partition eligible family evidence.')

        for board in self.categories:
            if any(
                not bounded(
                    row.votes, row.effective_votes, board.votes, board.effective_votes
                )
                for row in board.standings
            ):
                raise ValueError('Category standings cannot exceed category evidence.')

        if not bounded(
            sum(row.votes for row in self.standings),
            sum(row.effective_votes for row in self.standings),
            2 * self.votes,
            2 * self.effective_votes,
        ):
            raise ValueError('Standing totals cannot exceed both sides of each vote.')

        heads = {(row.system, row.opponent): row for row in self.head_to_head}
        if (
            len(heads) != len(self.head_to_head)
            or sum(row.votes for row in self.head_to_head) != 2 * self.votes
            or not close(
                sum(row.effective_votes for row in self.head_to_head),
                2 * self.effective_votes,
            )
        ):
            raise ValueError(
                'Head-to-head totals must account for both sides of each vote.'
            )

        for (system, opponent), row in heads.items():
            mirror = heads.get((opponent, system))
            if (
                system == opponent
                or mirror is None
                or row.votes != mirror.votes
                or not close(row.effective_votes, mirror.effective_votes)
                or row.wins != mirror.losses
                or row.losses != mirror.wins
                or row.ties != mirror.ties
                or row.both_bad != mirror.both_bad
                or row.wins + row.losses + row.ties + row.both_bad != row.votes
                or not close(row.win_rate + mirror.win_rate, 1)
            ):
                raise ValueError('Head-to-head rows must describe mirrored choices.')


# ── Tests Schema ──


class TestsStanding(_Model):
    """One system's pass rate, with cost and latency over its error-free trials."""

    system: str
    trials: int
    errored: int
    pass_rate: float
    mean_cost_usd: float | None
    p50_latency_sec: float | None


class TestsResults(_Model):
    """Pass rates on Harbor task tests, where an errored trial fails."""

    scorer: Literal['tests']
    standings: list[TestsStanding]


# ── Reports ──


type Results = Annotated[VotesResults | TestsResults, Field(discriminator='scorer')]
type RoundName = Annotated[str, Field(pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')]


class Report(_Model):
    """A closed round of one family, written to `reports/<family>/<round>.json`."""

    family: FamilyName
    round: RoundName
    closed_at: AwareDatetime
    systems: dict[Annotated[str, Field(min_length=1)], PublicSystem]
    results: Results

    @model_validator(mode='after')
    def validate_systems(self) -> Report:
        referenced = _referenced_systems(self.results)
        if any(not system.strip() for system in referenced):
            raise ValueError('System identifiers must contain text.')
        if set(self.systems) != referenced:
            raise ValueError(
                'Public metadata must match every referenced system exactly.'
            )

        names = [system.display_name for system in self.systems.values()]
        if len(set(names)) != len(names):
            raise ValueError('Public display names must be unique within a report.')

        unranked: list[str] = []
        if isinstance(self.results, VotesResults):
            unranked = self.results.unranked
            for board in self.results.categories:
                _validate_memberships(
                    [row.system for row in board.standings], board.unranked
                )
            if self.results.anchor not in {
                row.system for row in self.results.standings
            }:
                raise ValueError('The anchor must have an overall standing.')

        _validate_memberships([row.system for row in self.results.standings], unranked)

        return self


def _validate_memberships(ranked: list[str], unranked: list[str]) -> None:
    members = ranked + unranked
    if len(set(members)) != len(members):
        raise ValueError('Ranked and unranked memberships must be distinct and unique.')


def _referenced_systems(results: Results) -> set[str]:
    systems = {row.system for row in results.standings}
    if isinstance(results, VotesResults):
        systems.update((results.reference, results.anchor))
        systems.update(results.unranked)
        for row in results.head_to_head:
            systems.update((row.system, row.opponent))
        for board in results.categories:
            systems.update(row.system for row in board.standings)
            systems.update(board.unranked)

    return systems


def close(
    family: Family,
    closed_at: datetime,
    *,
    system_names: Mapping[str, str],
    previous_reports: Sequence[Report],
) -> Report:
    """Aggregate current test-scored outputs under their public system names."""
    if not isinstance(family.settings, TaskTestsSettings):
        raise ValueError('Votes require a saved canonical D1 export.')

    closed_at = TypeAdapter(AwareDatetime).validate_python(closed_at).astimezone(UTC)
    categories = {case.id: case.category for case in load_cases(family)}
    outputs = [
        output for output in read_outputs(family.name) if output.case in categories
    ]

    if not outputs:
        raise ValueError(f'Family `{family.name}` has no outputs on its current cases.')

    fingerprints = _fingerprints(outputs)
    results = _tests_results(outputs)

    return Report(
        family=family.name,
        round=closed_at.date().isoformat(),
        closed_at=closed_at,
        systems=_public_systems(
            _referenced_systems(results),
            fingerprints,
            system_names,
            previous_reports,
            family.name,
            closed_at,
        ),
        results=results,
    )


def close_snapshot(
    analysis: AnalysisInput,
    closed_at: datetime,
    *,
    fingerprints: Mapping[str, str],
    system_names: Mapping[str, str],
    previous_reports: Sequence[Report],
) -> Report:
    """Close frozen evidence under the fingerprints and names its release recorded."""
    closed_at = TypeAdapter(AwareDatetime).validate_python(closed_at).astimezone(UTC)
    results = votes_results(analysis)
    return Report(
        family=analysis.family,
        round=closed_at.date().isoformat(),
        closed_at=closed_at,
        systems=_public_systems(
            _referenced_systems(results),
            fingerprints,
            system_names,
            previous_reports,
            analysis.family,
            closed_at,
        ),
        results=results,
    )


def _public_systems(
    referenced: set[str],
    fingerprints: Mapping[str, str | None],
    names: Mapping[str, str],
    previous_reports: Sequence[Report],
    family: str,
    closed_at: datetime,
) -> dict[str, PublicSystem]:
    history: dict[str, PublicSystem] = {}
    ordered = sorted(previous_reports, key=lambda report: report.closed_at)
    for previous in ordered:
        if previous.family != family or previous.closed_at >= closed_at:
            raise ValueError(
                'Prior reports must belong to this family and close earlier.'
            )
        history.update(previous.systems)
    frozen = _frozen_fingerprints(ordered)

    public: dict[str, PublicSystem] = {}
    for system in sorted(referenced):
        fingerprint = fingerprints.get(system)
        prior = history.get(system)
        known = frozen.get(system)
        if known is not None and fingerprint is not None and known != fingerprint:
            raise ValueError(f'System `{system}` changed its published fingerprint.')
        if system in names:
            public[system] = PublicSystem(
                display_name=names[system], fingerprint=fingerprint
            )
        elif (
            prior is not None
            and fingerprint is not None
            and prior.fingerprint == fingerprint
        ):
            public[system] = prior
        else:
            raise ValueError(
                f'System `{system}` lacks verified public metadata. Restore its '
                'metadata or close a report before retiring it.'
            )

    return public


def write_report(report: Report) -> Path:
    TypeAdapter(FamilyName).validate_python(report.family)
    TypeAdapter(RoundName).validate_python(report.round)

    path = REPORTS_DIR / report.family / f'{report.round}.json'
    write_atomically(path, f'{report.model_dump_json(indent=2)}\n')
    return path


def read_reports(family: str, before: datetime) -> list[Report]:
    family = TypeAdapter(FamilyName).validate_python(family)
    before = TypeAdapter(AwareDatetime).validate_python(before)
    reports = [report for report in _closed(family) if report.closed_at < before]
    return sorted(reports, key=lambda report: report.closed_at)


def reported_fingerprints(family: str) -> dict[str, str | None]:
    """Map each system a closed report of the family names to the fingerprint it froze.

    A system maps to null when no report recorded its fingerprint.

    Raises
    ------
    ValueError
        Two closed reports froze different fingerprints under one system.
    """
    family = TypeAdapter(FamilyName).validate_python(family)
    return _frozen_fingerprints(_closed(family))


def _frozen_fingerprints(reports: Iterable[Report]) -> dict[str, str | None]:
    frozen: dict[str, str | None] = {}
    for report in reports:
        for system, metadata in report.systems.items():
            known = frozen.get(system)
            if metadata.fingerprint is None:
                frozen.setdefault(system, None)
            elif known is not None and known != metadata.fingerprint:
                raise ValueError(
                    f'System `{system}` reuses an identifier across fingerprints.'
                )
            else:
                frozen[system] = metadata.fingerprint
    return frozen


def _closed(family: str) -> list[Report]:
    reports: list[Report] = []
    for path in sorted((REPORTS_DIR / family).glob('*.json')):
        report = Report.model_validate_json(path.read_text())
        if report.family != family or report.round != path.stem:
            raise ValueError(
                f'Report `{path.name}` does not match its family and round path.'
            )
        reports.append(report)
    return reports


def _fingerprints(outputs: list[Output]) -> dict[str, str | None]:
    """Map each system with outputs to the one fingerprint they all carry.

    Raises
    ------
    ValueError
        A system has outputs under several fingerprints.
    """
    by_system: defaultdict[str, set[str | None]] = defaultdict(set)
    for output in outputs:
        by_system[output.system].add(output.fingerprint)

    for system, fingerprints in sorted(by_system.items()):
        if len(fingerprints) > 1:
            raise ValueError(
                f'System `{system}` has outputs under several fingerprints. Give '
                'the changed system a new name, as `docs/guide/authoring.md#systems` '
                'describes.'
            )

    return {system: fingerprints.pop() for system, fingerprints in by_system.items()}


# ── Votes Inputs ──


@dataclass(frozen=True)
class HumanEvidence:
    receipt: str
    actor: str
    session: str
    choice: Choice
    defects_a: tuple[Defect, ...]
    defects_b: tuple[Defect, ...]
    length_a: int
    length_b: int
    comparison: ranking.Comparison
    """Weight assigned once over the complete eligible family evaluation."""

    repeat_of: str | None = None

    @property
    def systems(self) -> tuple[str, str]:
        return self.comparison.left, self.comparison.right


@dataclass(frozen=True)
class OutputMeasurement:
    case: str
    system: str
    errored: bool
    verdict: Literal['pass', 'fail', 'ungraded']
    cost_usd: float | None
    latency_sec: float | None
    output_tokens: int | None


@dataclass(frozen=True)
class AnalysisInput:
    """One complete frozen family evaluation, closed under `METHOD` and `RULES`."""

    family: str
    reference: str
    systems: tuple[str, ...]
    """Frozen system order, which breaks ties between weighted roots."""

    defects: tuple[Defect, ...]
    provenance: LedgerProvenance
    categories: tuple[tuple[str, str | None], ...]
    """Every frozen case, with None marking an explicitly uncategorized case."""

    ordinary: tuple[HumanEvidence, ...]
    repeats: tuple[HumanEvidence, ...]
    settlements: tuple[ranking.Comparison, ...]
    outputs: tuple[OutputMeasurement, ...]
    sessions: tuple[str, ...]
    """Eligible session IDs ordered by their frozen start time and stable ID."""

    accepted: int
    """Every accepted choice, including repeats and inactive receipts."""


# ── Votes Results ──


def votes_results(analysis: AnalysisInput) -> VotesResults:
    """Fit one complete frozen evaluation without reading current source files."""
    votes = list(analysis.ordinary)
    outputs = list(analysis.outputs)
    comparisons = [row.comparison for row in votes] + list(analysis.settlements)
    if not comparisons:
        raise ValueError(f'Family `{analysis.family}` has no eligible comparisons.')

    board, *_ = ranking.rate_components(
        comparisons, analysis.systems, analysis.reference
    )
    ratings = board.ratings
    ranked = set(ratings.systems)
    connected = [row for row in votes if set(row.systems) <= ranked]
    feature = _length_feature(connected)
    length = (
        None
        if feature is None
        else ranking.fit(
            ranking.Matchups.build([row.comparison for row in connected]),
            feature,
        ).covariate
    )

    standings = [
        _standing(
            system,
            float(ratings.score[i]),
            int((ratings.score > ratings.score[i]).sum()) + 1,
            [vote for vote in votes if system in vote.systems],
            [output for output in outputs if output.system == system],
            analysis.defects,
        )
        for i, system in enumerate(ratings.systems)
    ]
    standings.sort(key=lambda standing: (standing.rank, standing.system))
    return VotesResults(
        scorer='votes',
        method=METHOD,
        rules=RULES,
        inference=INFERENCE,
        provenance=analysis.provenance,
        reference=analysis.reference,
        anchor=board.anchor,
        votes=len(votes),
        effective_votes=sum(row.comparison.weight for row in votes),
        dropped_votes=analysis.accepted - len(votes),
        sessions=len(analysis.sessions),
        standings=standings,
        unranked=sorted({output.system for output in outputs} - ranked),
        head_to_head=_head_to_head(
            votes, sorted({system for row in votes for system in row.systems})
        ),
        categories=_categories(
            dict(analysis.categories), board.anchor, votes, comparisons, outputs
        ),
        position=ratings.position if connected else None,
        length=None if length is None else ranking.ELO_PER_LOGIT * length,
        separability=None,
        split_half_tau=_split_half_tau(votes, analysis.sessions, board.root),
        rater_agreement=_rater_agreement(
            votes, analysis.repeats, RULES.rater_agreement_floor
        ),
        decision=None,
    )


def _standing(
    system: str,
    score: float,
    rank: int,
    votes: list[HumanEvidence],
    outputs: list[OutputMeasurement],
    listed: Sequence[Defect],
) -> Standing:
    choices = Counter(vote.choice for vote in votes)
    defects = Counter(
        defect
        for vote in votes
        for side, tagged in zip(
            vote.systems, (vote.defects_a, vote.defects_b), strict=True
        )
        if side == system
        for defect in tagged
    )

    finished = [output for output in outputs if not output.errored]
    graded = [output for output in outputs if output.verdict != 'ungraded']
    decisive = choices['a'] + choices['b']
    effective = sum(vote.comparison.weight for vote in votes)

    return Standing(
        system=system,
        score=score,
        interval=None,
        rank=rank,
        best_rank=None,
        worst_rank=None,
        votes=len(votes),
        effective_votes=effective,
        decisive_votes=decisive,
        both_bad_rate=(
            sum(vote.comparison.weight for vote in votes if vote.choice == 'both_bad')
            / effective
            if effective
            else None
        ),
        outputs=len(finished),
        gate_failure_rate=_share(output.verdict == 'fail' for output in graded),
        mean_cost_usd=_mean(output.cost_usd for output in finished),
        p50_latency_sec=_median(output.latency_sec for output in finished),
        output_tokens=_distribution(output.output_tokens for output in finished),
        latency_sec=_distribution(output.latency_sec for output in finished),
        defects={defect: defects[defect] for defect in listed},
    )


def _head_to_head(
    votes: list[HumanEvidence], systems: Iterable[str]
) -> list[HeadToHead]:
    tallies: defaultdict[tuple[str, str], Counter[Choice]] = defaultdict(Counter)
    weighted: defaultdict[tuple[str, str], defaultdict[Choice, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for vote in votes:
        left, right = vote.systems
        tallies[(left, right)][vote.choice] += 1
        tallies[(right, left)][SWAPPED[vote.choice]] += 1
        weighted[(left, right)][vote.choice] += vote.comparison.weight
        weighted[(right, left)][SWAPPED[vote.choice]] += vote.comparison.weight

    rows: list[HeadToHead] = []
    for system in systems:
        for opponent in systems:
            tally = tallies.get((system, opponent))
            if tally is None:
                continue

            total = tally.total()
            effective = weighted[(system, opponent)]
            mass = sum(effective.values())
            rows.append(
                HeadToHead(
                    system=system,
                    opponent=opponent,
                    votes=total,
                    effective_votes=mass,
                    wins=tally['a'],
                    losses=tally['b'],
                    ties=tally['tie'],
                    both_bad=tally['both_bad'],
                    win_rate=sum(
                        ranking.LEFT_SCORE[c] * n for c, n in effective.items()
                    )
                    / mass,
                )
            )

    return rows


def _categories(
    categories: Mapping[str, str | None],
    anchor: str,
    votes: list[HumanEvidence],
    comparisons: list[ranking.Comparison],
    outputs: list[OutputMeasurement],
) -> list[CategoryBoard]:
    """Refit each category in the board anchor's component, anchored there too."""
    category_votes: defaultdict[str, list[HumanEvidence]] = defaultdict(list)
    for row in votes:
        if (category := categories[row.comparison.case]) is not None:
            category_votes[category].append(row)

    by_category: defaultdict[str, list[ranking.Comparison]] = defaultdict(list)
    for comparison in comparisons:
        if (category := categories[comparison.case]) is not None:
            by_category[category].append(comparison)

    entrants: defaultdict[str, set[str]] = defaultdict(set)
    for output in outputs:
        if (category := categories[output.case]) is not None:
            entrants[category].add(output.system)

    boards: list[CategoryBoard] = []
    for name, category_comparisons in sorted(by_category.items()):
        matchups = ranking.Matchups.build(category_comparisons)
        if anchor not in matchups.systems:
            continue

        ratings = ranking.rate_point(matchups.component(anchor), anchor)

        standings: list[CategoryStanding] = []
        for i, system in enumerate(ratings.systems):
            mine = [row for row in category_votes[name] if system in row.systems]
            decisive = sum(vote.choice in ('a', 'b') for vote in mine)
            standings.append(
                CategoryStanding(
                    system=system,
                    score=float(ratings.score[i]),
                    interval=None,
                    votes=len(mine),
                    effective_votes=sum(row.comparison.weight for row in mine),
                    decisive_votes=decisive,
                )
            )

        boards.append(
            CategoryBoard(
                category=name,
                votes=len(category_votes[name]),
                effective_votes=sum(
                    row.comparison.weight for row in category_votes[name]
                ),
                standings=standings,
                unranked=sorted(entrants[name] - set(ratings.systems)),
            )
        )

    return boards


def _length_feature(votes: list[HumanEvidence]) -> ranking.Floats | None:
    """Return the z-scored (A − B) / (A + B) of each vote's output lengths.

    Returns None when every vote has the same relative length difference.
    """
    a = np.array([row.length_a for row in votes])
    b = np.array([row.length_b for row in votes])
    total = a + b
    relative = np.divide(a - b, total, out=np.zeros(len(votes)), where=total > 0)
    if not votes or np.ptp(relative) == 0:
        return None

    weights = np.array([row.comparison.weight for row in votes])
    centered = relative - np.average(relative, weights=weights)
    scale = np.sqrt(np.average(centered**2, weights=weights))
    feature: ranking.Floats = centered / scale
    return feature


def _split_half_tau(
    votes: list[HumanEvidence], sessions: Sequence[str], root: str
) -> float | None:
    """Compare the systems both halves connect to the root, if each has it.

    `sessions` lists the sessions holding `votes` in the order they started, and
    alternate sessions form each half.
    """
    parity = {session: i % 2 for i, session in enumerate(sessions)}
    halves = [
        [row.comparison for row in votes if parity[row.session] == half]
        for half in (0, 1)
    ]
    if not all(any(root in (c.left, c.right) for c in half) for half in halves):
        return None

    first, second = (
        dict(zip(fitted.systems, ranking.fit(fitted).strength, strict=True))
        for fitted in (ranking.Matchups.build(half).component(root) for half in halves)
    )
    shared = sorted(first.keys() & second.keys())
    return ranking.rank_agreement(
        np.array([first[s] for s in shared]), np.array([second[s] for s in shared])
    )


def _rater_agreement(
    votes: Sequence[HumanEvidence], repeats: Sequence[HumanEvidence], floor: float
) -> RaterAgreement:
    originals = {vote.receipt: vote for vote in votes}
    matches = [
        vote.choice == SWAPPED[originals[vote.repeat_of].choice]
        for vote in repeats
        if vote.repeat_of in originals
    ]
    agreement = _share(matches)

    return RaterAgreement(
        repeats=len(matches),
        agreement=agreement,
        below_floor=agreement is not None and agreement < floor,
    )


# ── Tests Results ──


def _tests_results(outputs: list[Output]) -> TestsResults:
    by_system: defaultdict[str, list[Output]] = defaultdict(list)
    for output in outputs:
        by_system[output.system].append(output)

    standings: list[TestsStanding] = []
    for system, trials in sorted(by_system.items()):
        finished = [output for output in trials if output.error is None]
        standings.append(
            TestsStanding(
                system=system,
                trials=len(trials),
                errored=len(trials) - len(finished),
                pass_rate=sum(output.reward or 0.0 for output in finished)
                / len(trials),
                mean_cost_usd=_mean(output.cost_usd for output in finished),
                p50_latency_sec=_median(output.latency_sec for output in finished),
            )
        )

    return TestsResults(scorer='tests', standings=standings)


# ── Aggregates ──


def _share(flags: Iterable[bool]) -> float | None:
    values = list(flags)
    return sum(values) / len(values) if values else None


def _mean(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return statistics.fmean(known) if known else None


def _median(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return statistics.median(known) if known else None


def _distribution(values: Iterable[float | None]) -> Distribution | None:
    known = [value for value in values if value is not None]
    if not known:
        return None

    p10, p50, p90 = np.percentile(known, [10, 50, 90])
    return Distribution(p10=float(p10), p50=float(p50), p90=float(p90))
