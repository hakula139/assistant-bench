"""The report schema and inputs, the fixed votes method, and the writer of rounds.

A report holds aggregates under public system names and nothing else: no case,
instruction, output text, route, or request ID.
"""

import statistics
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
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
    Category,
    Choice,
    Defect,
    Family,
    FamilyName,
    load_cases,
)
from assistant_bench.outputs import (
    Output,
    read_outputs,
    system_fingerprints,
    tests_pass,
)
from assistant_bench.paths import REPORTS_DIR, write_atomically
from assistant_bench.scoring import pass_rates, ranking, weighting


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


type Share = Annotated[float, Field(ge=0, le=1)]

type CostGap = Literal['unpriced', 'unreported']


def _check_cost_gap(
    mean_cost_usd: float | None, cost_gap: CostGap | None, finished: int
) -> None:
    if (cost_gap is None) != (mean_cost_usd is not None or finished == 0):
        raise ValueError(
            'A cost gap is present exactly when finished outputs lack a mean cost.'
        )


class PassRate(_Model):
    """A mean of per-task shares with its 95% Clopper-Pearson interval over tasks."""

    rate: Share
    lower: Share
    upper: Share


# ── Votes Method ──


class VotesMethod(_Model):
    l2_penalty: Annotated[float, Field(gt=0)]
    """Penalty on system logits, excluding the sided intercept."""

    points_per_logit: Annotated[float, Field(gt=0)]
    anchor_score: float
    actor_cap: Annotated[int, Field(strict=True, gt=0)]
    """Each of an actor's `n` eligible choices weighs `min(1, actor_cap / n)`."""


class VotesRules(_Model):
    preliminary_decisive_votes: Annotated[int, Field(strict=True, gt=0)]
    """Raw decisive votes below which a standing is preliminary."""


METHOD = VotesMethod(
    l2_penalty=ranking.L2_PENALTY,
    points_per_logit=ranking.ELO_PER_LOGIT,
    anchor_score=ranking.ANCHOR,
    actor_cap=weighting.CAP,
)

RULES = VotesRules(preliminary_decisive_votes=40)


# ── Votes Schema ──


class Cutoff(_Model):
    """What closing read from one store, which freshness compares with the store."""

    id: Annotated[int, Field(strict=True, ge=0)]
    """The largest vote ID older than the undo window, or 0 when the store held none."""
    votes: Annotated[int, Field(strict=True, ge=0)]
    """The store's votes with IDs up to `id` that no exclusion removed."""


class Cutoffs(_Model):
    """Each store's cutoff at closing, null for a store closing did not read."""

    production: Cutoff | None
    local: Cutoff | None


class Standing(_Model):
    system: str
    score: float
    """Point estimate on the round's recorded score scale."""

    rank: int
    """Competition rank of the point score."""

    votes: Annotated[int, Field(ge=0)]
    effective_votes: Annotated[float, Field(ge=0)]
    """Eligible human row weight, excluding settled comparisons."""
    decisive_votes: Annotated[int, Field(ge=0)]
    both_bad_rate: float | None

    outputs: int
    """Outputs that finished without an error."""
    gate_failure_rate: float | None
    """Failure share among outputs with a verdict, including errored agents."""
    pass_rate: PassRate | None
    """The tests pass rate over tasks, null when the system's outputs lack rewards."""

    mean_cost_usd: float | None
    """Null unless every finished output's cost is known, like the other aggregates."""
    cost_gap: CostGap | None
    """Why finished outputs lack a mean cost: `unpriced` when an output with known
    usage has no cost, and `unreported` when only outputs without usage do."""

    p50_latency_sec: float | None
    output_tokens: Distribution | None
    latency_sec: Distribution | None

    defects: dict[Defect, int]
    """Raw tagged-vote counts in the family's defect order."""

    @model_validator(mode='after')
    def validate_cost_gap(self) -> Standing:
        _check_cost_gap(self.mean_cost_usd, self.cost_gap, self.outputs)
        return self


class HeadToHead(_Model):
    """Human choices between two systems, with half credit for either tie.

    The outcome counts are raw choices, while `effective_votes` and `win_rate` use
    the actors' weights.
    """

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
    votes: Annotated[int, Field(ge=0)]
    effective_votes: Annotated[float, Field(ge=0)]
    decisive_votes: Annotated[int, Field(ge=0)]


class CategoryBoard(_Model):
    """A category point fit in the overall anchor's connected component."""

    category: Category
    votes: Annotated[int, Field(ge=0)]
    effective_votes: Annotated[float, Field(ge=0)]
    standings: list[CategoryStanding]
    unranked: list[str]


class VotesResults(_Model):
    """Point estimates and descriptive diagnostics on eligible comparisons."""

    scorer: Literal['votes']
    method: VotesMethod
    rules: VotesRules
    cutoffs: Cutoffs

    reference: str
    """Configured reference, whether or not comparisons connect it."""
    anchor: str
    """Ranked system fixed at the recorded anchor score."""

    votes: Annotated[int, Field(ge=0)]
    """Eligible human choices across every component."""
    effective_votes: Annotated[float, Field(ge=0)]
    """Eligible human row weight across every component, excluding settlements."""
    dropped_votes: int
    """Excluded votes plus votes on outputs the publications no longer admit."""
    voters: int
    """Browsers with at least one eligible human choice."""

    standings: list[Standing]
    unranked: list[str]
    """Entrants in the frozen evaluation outside the comparison component."""
    head_to_head: list[HeadToHead]
    categories: list[CategoryBoard]

    position: float | None
    """Left-pane advantage in score points, null without ranked human rows."""
    length: float | None
    """Score points per length-difference standard deviation, if identifiable."""
    split_half_tau: float | None
    """Order agreement between alternating eligible votes, when measurable."""


# ── Tests Schema ──


class TestsStanding(_Model):
    """One system's pass rates, with cost and latency over its error-free trials."""

    system: str
    tasks: Annotated[int, Field(ge=1)]
    trials: Annotated[int, Field(ge=1)]
    errored: Annotated[int, Field(ge=0)]
    pass_rate: PassRate
    pass_hat_k: Share | None
    """Share of tasks whose `samples` trials all passed, null for one sample."""

    mean_cost_usd: float | None
    """Null unless every finished trial's cost is known, like the median latency."""
    cost_gap: CostGap | None
    """Why finished trials lack a mean cost: `unpriced` when a trial with known
    usage has no cost, and `unreported` when only trials without usage do."""

    p50_latency_sec: float | None

    @model_validator(mode='after')
    def validate_cost_gap(self) -> TestsStanding:
        _check_cost_gap(self.mean_cost_usd, self.cost_gap, self.trials - self.errored)
        return self


class TestsCategoryStanding(_Model):
    system: str
    tasks: Annotated[int, Field(ge=1)]
    pass_rate: PassRate


class TestsCategory(_Model):
    category: str
    standings: list[TestsCategoryStanding]


class TestsDecision(_Model):
    """The top pass rate and the systems a paired sign test cannot separate from it."""

    leader: str
    tied: list[str]


class TestsResults(_Model):
    """Pass rates on Harbor task tests, where every system ran every task."""

    scorer: Literal['tests']
    samples: Annotated[int, Field(ge=1)]
    """Trials the family runs per task and system, the k of pass^k."""

    standings: Annotated[list[TestsStanding], Field(min_length=1)]
    categories: list[TestsCategory]
    decision: TestsDecision

    @model_validator(mode='after')
    def validate_rates(self) -> TestsResults:
        if any(
            (row.pass_hat_k is None) != (self.samples == 1) for row in self.standings
        ):
            raise ValueError('pass^k is present exactly when tasks run several times.')

        systems = {row.system for row in self.standings}
        leader, tied = self.decision.leader, self.decision.tied
        if (
            leader not in systems
            or len(set(tied)) != len(tied)
            or not set(tied) <= systems - {leader}
        ):
            raise ValueError('The decision must name standing systems once.')

        names = [board.category for board in self.categories]
        if len(set(names)) != len(names) or any(
            not {row.system for row in board.standings} <= systems
            for board in self.categories
        ):
            raise ValueError('Categories must be unique and list standing systems.')

        return self


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
    samples: int,
    system_names: Mapping[str, str],
    frozen_fingerprints: Mapping[str, str | None],
) -> Report:
    """Aggregate a tests family's current outputs under their public system names.

    `closed_at` is an aware time, and its UTC date names the round.
    """
    closed_at = closed_at.astimezone(UTC)
    categories = {case.id: case.category for case in load_cases(family)}
    outputs = [
        output for output in read_outputs(family.name) if output.case in categories
    ]

    if not outputs:
        raise ValueError(f'Family `{family.name}` has no outputs on its current cases.')

    _require_every_trial(outputs, categories, samples)
    results = _tests_results(outputs, categories, samples)

    return Report(
        family=family.name,
        round=closed_at.date().isoformat(),
        closed_at=closed_at,
        systems=_public_systems(
            _referenced_systems(results),
            system_fingerprints(outputs),
            system_names,
            frozen_fingerprints,
        ),
        results=results,
    )


def close_votes(
    analysis: AnalysisInput,
    closed_at: datetime,
    *,
    fingerprints: Mapping[str, str],
    system_names: Mapping[str, str],
    frozen_fingerprints: Mapping[str, str | None],
) -> Report:
    """Close a round's votes under the fingerprints and names its publications record.

    `closed_at` is an aware time, and its UTC date names the round.
    """
    closed_at = closed_at.astimezone(UTC)
    results = votes_results(analysis)
    return Report(
        family=analysis.family,
        round=closed_at.date().isoformat(),
        closed_at=closed_at,
        systems=_public_systems(
            _referenced_systems(results),
            fingerprints,
            system_names,
            frozen_fingerprints,
        ),
        results=results,
    )


def _public_systems(
    referenced: set[str],
    fingerprints: Mapping[str, str | None],
    names: Mapping[str, str],
    frozen: Mapping[str, str | None],
) -> dict[str, PublicSystem]:
    """Name each referenced system after checking it keeps its frozen fingerprint."""
    public: dict[str, PublicSystem] = {}
    for system in sorted(referenced):
        fingerprint = fingerprints.get(system)
        known = frozen.get(system)
        if known is not None and fingerprint is not None and known != fingerprint:
            raise ValueError(f'System `{system}` changed its published fingerprint.')

        public[system] = PublicSystem(
            display_name=names[system], fingerprint=fingerprint
        )

    return public


def write_report(report: Report) -> Path:
    path = REPORTS_DIR / report.family / f'{report.round}.json'
    write_atomically(path, f'{report.model_dump_json(indent=2)}\n')
    return path


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


# ── Votes Inputs ──


@dataclass(frozen=True)
class HumanEvidence:
    receipt: str
    actor: str
    choice: Choice
    defects_a: tuple[Defect, ...]
    defects_b: tuple[Defect, ...]
    length_a: int
    length_b: int
    comparison: ranking.Comparison
    """Weight assigned once over the complete eligible family evaluation."""

    @property
    def systems(self) -> tuple[str, str]:
        return self.comparison.left, self.comparison.right


@dataclass(frozen=True)
class OutputMeasurement:
    case: str
    system: str
    errored: bool
    verdict: Literal['pass', 'fail', 'ungraded']
    reward: float | None
    """The task tests' reward, absent for rendered cases and untested trials."""

    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_sec: float | None


@dataclass(frozen=True)
class AnalysisInput:
    """One complete frozen family evaluation, closed under `METHOD` and `RULES`."""

    family: str
    reference: str
    systems: tuple[str, ...]
    """Frozen system order, which breaks ties between weighted roots."""

    defects: tuple[Defect, ...]
    cutoffs: Cutoffs
    declared_categories: tuple[Category, ...]
    """The family's declared category IDs, whose order the boards keep."""
    categories: tuple[tuple[str, Category | None], ...]
    """Every frozen case, with None marking an explicitly uncategorized case."""

    ordinary: tuple[HumanEvidence, ...]
    """Eligible human choices, ordered by store and then by vote ID."""

    settlements: tuple[ranking.Comparison, ...]
    outputs: tuple[OutputMeasurement, ...]
    dropped: int
    """Votes left out of `ordinary`, including excluded ones."""


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
        cutoffs=analysis.cutoffs,
        reference=analysis.reference,
        anchor=board.anchor,
        votes=len(votes),
        effective_votes=sum(row.comparison.weight for row in votes),
        dropped_votes=analysis.dropped,
        voters=len({row.actor for row in votes}),
        standings=standings,
        unranked=sorted({output.system for output in outputs} - ranked),
        head_to_head=_head_to_head(
            votes, sorted({system for row in votes for system in row.systems})
        ),
        categories=_categories(
            dict(analysis.categories),
            analysis.declared_categories,
            board.anchor,
            votes,
            comparisons,
            outputs,
        ),
        position=ratings.position if connected else None,
        length=None if length is None else ranking.ELO_PER_LOGIT * length,
        split_half_tau=_split_half_tau(votes, board.root),
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
        rank=rank,
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
        pass_rate=(
            _pass_rate(_pass_shares(outputs).values())
            if any(output.reward is not None for output in outputs)
            else None
        ),
        mean_cost_usd=_mean(output.cost_usd for output in finished),
        cost_gap=_cost_gap(finished),
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
    categories: Mapping[str, Category | None],
    declared: tuple[Category, ...],
    anchor: str,
    votes: list[HumanEvidence],
    comparisons: list[ranking.Comparison],
    outputs: list[OutputMeasurement],
) -> list[CategoryBoard]:
    """Refit each declared category in the anchor's component, anchored there too."""
    category_votes: defaultdict[Category, list[HumanEvidence]] = defaultdict(list)
    for row in votes:
        if (category := categories[row.comparison.case]) is not None:
            category_votes[category].append(row)

    by_category: defaultdict[Category, list[ranking.Comparison]] = defaultdict(list)
    for comparison in comparisons:
        if (category := categories[comparison.case]) is not None:
            by_category[category].append(comparison)

    entrants: defaultdict[Category, set[str]] = defaultdict(set)
    for output in outputs:
        if (category := categories[output.case]) is not None:
            entrants[category].add(output.system)

    boards: list[CategoryBoard] = []
    for name in declared:
        if name not in by_category:
            continue

        matchups = ranking.Matchups.build(by_category[name])
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


def _split_half_tau(votes: list[HumanEvidence], root: str) -> float | None:
    """Compare the systems both halves connect to the root, if each has it.

    Alternate votes form each half, since a round may have only two or three
    voters.
    """
    halves = [[row.comparison for row in votes[half::2]] for half in (0, 1)]
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


# ── Tests Results ──


def _require_every_trial(
    outputs: Sequence[Output], tasks: Collection[str], samples: int
) -> None:
    """Refuse a round in which a system lacks `samples` trials of a current task.

    Ranking systems over different tasks or trial counts would compare unlike
    evidence, and the sign test assumes equal trials per shared task.
    """
    trials = Counter((output.system, output.case) for output in outputs)
    missing = {
        system: short
        for system in sorted({output.system for output in outputs})
        if (short := sorted(task for task in tasks if trials[system, task] < samples))
    }
    if missing:
        listed = '; '.join(
            f'`{system}` on {", ".join(f"`{task}`" for task in short)}'
            for system, short in missing.items()
        )
        raise ValueError(
            f'Every system needs {samples} trials of each current task before '
            f'closing, but these lack some: {listed}. Run `uv run bench run '
            '--system <name>` for each, then close again.'
        )


def _tests_results(
    outputs: list[Output], categories: Mapping[str, str], samples: int
) -> TestsResults:
    by_system: defaultdict[str, list[Output]] = defaultdict(list)
    for output in outputs:
        by_system[output.system].append(output)

    shares = {system: _pass_shares(trials) for system, trials in by_system.items()}
    standings = sorted(
        (
            _tests_standing(system, trials, shares[system], samples)
            for system, trials in by_system.items()
        ),
        key=lambda row: (-row.pass_rate.rate, row.system),
    )
    leader = standings[0].system

    return TestsResults(
        scorer='tests',
        samples=samples,
        standings=standings,
        categories=_tests_categories(standings, shares, categories),
        decision=TestsDecision(
            leader=leader,
            tied=[
                row.system
                for row in standings[1:]
                if not _separated(shares[leader], shares[row.system])
            ],
        ),
    )


def _tests_standing(
    system: str,
    trials: Sequence[Output],
    shares: Mapping[str, float],
    samples: int,
) -> TestsStanding:
    """Summarize one system's trials and its pass share per task."""
    finished = [output for output in trials if output.error is None]

    return TestsStanding(
        system=system,
        tasks=len(shares),
        trials=len(trials),
        errored=len(trials) - len(finished),
        pass_rate=_pass_rate(shares.values()),
        pass_hat_k=(
            statistics.fmean(share == 1 for share in shares.values())
            if samples > 1
            else None
        ),
        mean_cost_usd=_mean(output.cost_usd for output in finished),
        cost_gap=_cost_gap(finished),
        p50_latency_sec=_median(output.latency_sec for output in finished),
    )


def _tests_categories(
    standings: Sequence[TestsStanding],
    shares: Mapping[str, Mapping[str, float]],
    categories: Mapping[str, str],
) -> list[TestsCategory]:
    boards: list[TestsCategory] = []
    for category in sorted(
        {categories[task] for row in shares.values() for task in row}
    ):
        rows: list[TestsCategoryStanding] = []
        for standing in standings:
            mine = [
                share
                for task, share in shares[standing.system].items()
                if categories[task] == category
            ]
            if mine:
                rows.append(
                    TestsCategoryStanding(
                        system=standing.system,
                        tasks=len(mine),
                        pass_rate=_pass_rate(mine),
                    )
                )
        boards.append(TestsCategory(category=category, standings=rows))

    return boards


def _separated(leader: Mapping[str, float], other: Mapping[str, float]) -> bool:
    shared = sorted(leader.keys() & other.keys())
    return pass_rates.separates(
        [leader[task] for task in shared], [other[task] for task in shared]
    )


def _pass_shares(trials: Iterable[Output | OutputMeasurement]) -> dict[str, float]:
    """Map each task to its share of trials whose tests passed."""
    passes: defaultdict[str, list[bool]] = defaultdict(list)
    for trial in trials:
        passes[trial.case].append(tests_pass(trial.reward))

    return {task: sum(flags) / len(flags) for task, flags in passes.items()}


def _pass_rate(shares: Iterable[float]) -> PassRate:
    """Average task shares and bound the mean by its interval over tasks."""
    values = list(shares)
    rate = statistics.fmean(values)
    lower, upper = pass_rates.clopper_pearson(rate, len(values))
    return PassRate(rate=rate, lower=lower, upper=upper)


# ── Aggregates ──


def _share(flags: Iterable[bool]) -> float | None:
    values = list(flags)
    return sum(values) / len(values) if values else None


def _known(values: Iterable[float | None]) -> list[float] | None:
    """Return the values only when every one is known.

    Missing values are not a random sample, as long agent runs lose usage more
    often, so an aggregate over the known ones would be biased.
    """
    collected = list(values)
    if not collected or None in collected:
        return None
    return [value for value in collected if value is not None]


def _mean(values: Iterable[float | None]) -> float | None:
    known = _known(values)
    return statistics.fmean(known) if known else None


def _median(values: Iterable[float | None]) -> float | None:
    known = _known(values)
    return statistics.median(known) if known else None


def _distribution(values: Iterable[float | None]) -> Distribution | None:
    known = _known(values)
    if not known:
        return None

    p10, p50, p90 = np.percentile(known, [10, 50, 90])
    return Distribution(p10=float(p10), p50=float(p50), p90=float(p90))


def _cost_gap(finished: Sequence[Output | OutputMeasurement]) -> CostGap | None:
    unknown = [output for output in finished if output.cost_usd is None]
    if not unknown:
        return None
    if any(
        output.input_tokens is not None and output.output_tokens is not None
        for output in unknown
    ):
        return 'unpriced'
    return 'unreported'
