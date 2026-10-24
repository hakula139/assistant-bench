"""The report schema, with one results variant per scorer, and the writer of rounds.

A report holds aggregates under public system names and nothing else: no case,
instruction, output text, route, or request ID.
"""

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, assert_never

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
from zhon import hanzi

from assistant_bench.arena.resolution import MatchedVote, reconcile, settled
from assistant_bench.arena.store import (
    LEFT_SCORE,
    SWAPPED,
    Vote,
    VoteStore,
)
from assistant_bench.families import (
    Family,
    TaskTestsSettings,
    VotesSettings,
    load_cases,
)
from assistant_bench.outputs import Output, read_outputs
from assistant_bench.paths import REPORTS_DIR, write_atomically
from assistant_bench.scoring import ranking
from assistant_bench_arena.models import Choice, Defect


PRELIMINARY_DECISIVE_VOTES = 40
RATER_AGREEMENT_FLOOR = 0.7

# A Han character, or a run of anything else up to whitespace or a Han character.
_LENGTH_UNIT = re.compile(f'[{hanzi.characters}]|[^\\s{hanzi.characters}]+')


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


# ── Votes Schema ──


class UnitHumanWeights(_Model):
    name: Literal['unit']
    version: Literal[1]
    repeat_handling: Literal['included']


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

    human_weights: UnitHumanWeights
    settled_weights: SettledWeights


class VotesRules(_Model):
    preliminary_decisive_votes: Annotated[int, Field(gt=0)]
    """Coverage threshold, independent of population inference availability."""

    preliminary_decisive_vote_unit: Literal['raw-votes']

    rater_agreement_floor: Annotated[float, Field(ge=0, le=1)]


class UnavailableInference(_Model):
    status: Literal['unavailable']
    target: Literal['new-cases-and-voters']
    reason: Literal['population-method-not-validated']


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

    reference: str
    """Configured reference, whether or not comparisons connect it."""
    anchor: str
    """Ranked system fixed at the recorded anchor score."""

    votes: Annotated[int, Field(ge=0)]
    """Eligible human choices across every component."""
    effective_votes: Annotated[float, Field(ge=0)]
    """Eligible human row weight across every component, excluding settlements."""
    dropped_votes: int
    """Recorded choices excluded by current cases, outputs or gates."""
    sessions: int
    """Sessions containing at least one eligible human choice."""

    standings: list[Standing]
    unranked: list[str]
    """Entrants with current outputs outside the selected comparison component."""
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
        if any(raw != effective for raw, effective in aggregates):
            raise ValueError(
                'Unit human weights require effective votes to equal raw votes.'
            )

        agreement = self.rater_agreement.agreement
        below = agreement is not None and agreement < self.rules.rater_agreement_floor
        if self.rater_agreement.below_floor != below:
            raise ValueError('Rater agreement must use the recorded agreement floor.')

        return self


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


class Report(_Model):
    """A closed round of one family, written to `reports/<family>/<round>.json`."""

    family: str
    round: str
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
    store: VoteStore,
    closed_at: datetime,
    *,
    system_names: Mapping[str, str],
    previous_reports: Sequence[Report],
) -> Report:
    """Aggregate the family's current outputs, and its votes when votes score it.

    Raises
    ------
    ValueError
        No eligible evidence exists or public identity and provenance are invalid.
    """
    closed_at = TypeAdapter(AwareDatetime).validate_python(closed_at).astimezone(UTC)
    categories = {case.id: case.category for case in load_cases(family)}
    outputs = [
        output for output in read_outputs(family.name) if output.case in categories
    ]

    if not outputs:
        step = (
            f' Run `uv run bench run {family.name}` first.'
            if family.settings.systems
            else ''
        )
        raise ValueError(
            f'Family `{family.name}` has no outputs on its current cases.{step}'
        )

    results: Results
    match family.settings:
        case VotesSettings():
            recorded = store.votes(family.name)
            voted = {system for vote in recorded for system in vote.systems}
            entrants = set(family.settings.systems) | voted
            outputs = [output for output in outputs if output.system in entrants]
            fingerprints = _fingerprints(outputs)
            results = _votes_results(
                family, family.settings, store, recorded, outputs, categories
            )
        case TaskTestsSettings():
            fingerprints = _fingerprints(outputs)
            results = _tests_results(outputs)
        case _:
            assert_never(family.settings)

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


def _public_systems(
    referenced: set[str],
    fingerprints: Mapping[str, str | None],
    names: Mapping[str, str],
    previous_reports: Sequence[Report],
    family: str,
    closed_at: datetime,
) -> dict[str, PublicSystem]:
    history: dict[str, PublicSystem] = {}
    known_fingerprints: dict[str, str] = {}
    for previous in sorted(previous_reports, key=lambda report: report.closed_at):
        if previous.family != family or previous.closed_at >= closed_at:
            raise ValueError(
                'Prior reports must belong to this family and close earlier.'
            )
        for system, metadata in previous.systems.items():
            if metadata.fingerprint is not None:
                known = known_fingerprints.get(system)
                if known is not None and known != metadata.fingerprint:
                    raise ValueError(
                        f'System `{system}` reuses an identifier across fingerprints.'
                    )
                known_fingerprints[system] = metadata.fingerprint
            history[system] = metadata

    public: dict[str, PublicSystem] = {}
    for system in sorted(referenced):
        fingerprint = fingerprints.get(system)
        prior = history.get(system)
        if (
            system in known_fingerprints
            and fingerprint is not None
            and known_fingerprints[system] != fingerprint
        ):
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
    path = REPORTS_DIR / report.family / f'{report.round}.json'
    write_atomically(path, f'{report.model_dump_json(indent=2)}\n')
    return path


def read_reports(family: str, before: datetime) -> list[Report]:
    before = TypeAdapter(AwareDatetime).validate_python(before)
    reports: list[Report] = []
    for path in sorted((REPORTS_DIR / family).glob('*.json')):
        report = Report.model_validate_json(path.read_text())
        if report.family != family or report.round != path.stem:
            raise ValueError(
                f'Report `{path.name}` does not match its family and round path.'
            )
        if report.closed_at < before:
            reports.append(report)

    return sorted(reports, key=lambda report: report.closed_at)


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


# ── Votes Results ──


def _votes_results(
    family: Family,
    settings: VotesSettings,
    store: VoteStore,
    recorded: list[Vote],
    outputs: list[Output],
    categories: dict[str, str],
) -> VotesResults:
    """Fit eligible comparisons, retaining every system connected to the root."""
    voted = {system for vote in recorded for system in vote.systems}
    votes = reconcile(recorded, outputs)
    human_votes = [m.vote for m in votes]
    comparisons = [vote.comparison() for vote in human_votes] + settled(outputs)

    if not comparisons:
        if not recorded:
            raise ValueError(
                f'Family `{family.name}` has no votes yet. Vote with '
                '`uv run bench serve`, then close the round.'
            )
        raise ValueError(
            f'Family `{family.name}` has votes, but none compares two of its '
            'current outputs. Vote on the current outputs, then close the round.'
        )

    everything = ranking.Matchups.build(comparisons)
    root = everything.root(settings.systems)

    ratings = ranking.rate_point(everything.component(root), root)
    anchor = settings.reference if settings.reference in ratings.systems else root
    ratings = ratings.anchored(anchor)
    ranked = set(ratings.systems)
    connected = [m for m in votes if set(m.vote.systems) <= ranked]
    feature = _length_feature(connected)
    length = (
        None
        if feature is None
        else ranking.fit(
            ranking.Matchups.build([m.vote.comparison() for m in connected]),
            feature,
        ).covariate
    )

    standings = [
        _standing(
            system,
            float(ratings.score[i]),
            int((ratings.score > ratings.score[i]).sum()) + 1,
            [vote for vote in human_votes if system in vote.systems],
            [output for output in outputs if output.system == system],
            settings.defects,
        )
        for i, system in enumerate(ratings.systems)
    ]
    standings.sort(key=lambda standing: (standing.rank, standing.system))
    counted = {vote.session for vote in human_votes}
    sessions = [s.id for s in store.sessions(family.name) if s.id in counted]

    return VotesResults(
        scorer='votes',
        method=VotesMethod(
            name='bradley-terry',
            version=1,
            l2_penalty=ranking.L2_PENALTY,
            points_per_logit=ranking.ELO_PER_LOGIT,
            anchor_score=ranking.ANCHOR,
            human_weights=UnitHumanWeights(
                name='unit', version=1, repeat_handling='included'
            ),
            settled_weights=SettledWeights(name='sample-pair-share', version=1),
        ),
        rules=VotesRules(
            preliminary_decisive_votes=PRELIMINARY_DECISIVE_VOTES,
            preliminary_decisive_vote_unit='raw-votes',
            rater_agreement_floor=RATER_AGREEMENT_FLOOR,
        ),
        inference=UnavailableInference(
            status='unavailable',
            target='new-cases-and-voters',
            reason='population-method-not-validated',
        ),
        reference=settings.reference,
        anchor=anchor,
        votes=len(votes),
        effective_votes=float(len(votes)),
        dropped_votes=len(recorded) - len(votes),
        sessions=len(sessions),
        standings=standings,
        unranked=sorted({output.system for output in outputs} - ranked),
        head_to_head=_head_to_head(human_votes, sorted(voted)),
        categories=_categories(
            categories, anchor, votes, comparisons, outputs, settings.systems
        ),
        position=ratings.position if connected else None,
        length=None if length is None else ranking.ELO_PER_LOGIT * length,
        separability=None,
        split_half_tau=_split_half_tau(votes, sessions, root),
        rater_agreement=_rater_agreement(human_votes),
        decision=None,
    )


def _standing(
    system: str,
    score: float,
    rank: int,
    votes: list[Vote],
    outputs: list[Output],
    listed: list[Defect],
) -> Standing:
    choices = Counter(vote.choice for vote in votes)
    defects = Counter(
        defect
        for vote in votes
        for side, tagged in ((vote.a, vote.defects.a), (vote.b, vote.defects.b))
        if side.system == system
        for defect in tagged
    )

    finished = [output for output in outputs if output.error is None]
    graded = [output for output in outputs if output.gate_failures is not None]
    decisive = choices['a'] + choices['b']

    return Standing(
        system=system,
        score=score,
        interval=None,
        rank=rank,
        best_rank=None,
        worst_rank=None,
        votes=len(votes),
        effective_votes=float(len(votes)),
        decisive_votes=decisive,
        both_bad_rate=_share(vote.choice == 'both_bad' for vote in votes),
        outputs=len(finished),
        gate_failure_rate=_share(bool(output.gate_failures) for output in graded),
        mean_cost_usd=_mean(output.cost_usd for output in finished),
        p50_latency_sec=_median(output.latency_sec for output in finished),
        output_tokens=_distribution(output.output_tokens for output in finished),
        latency_sec=_distribution(output.latency_sec for output in finished),
        defects={defect: defects[defect] for defect in listed},
    )


def _head_to_head(votes: list[Vote], systems: Iterable[str]) -> list[HeadToHead]:
    tallies: defaultdict[tuple[str, str], Counter[Choice]] = defaultdict(Counter)
    for vote in votes:
        left, right = vote.systems
        tallies[(left, right)][vote.choice] += 1
        tallies[(right, left)][SWAPPED[vote.choice]] += 1

    rows: list[HeadToHead] = []
    for system in systems:
        for opponent in systems:
            tally = tallies.get((system, opponent))
            if tally is None:
                continue

            total = tally.total()
            rows.append(
                HeadToHead(
                    system=system,
                    opponent=opponent,
                    votes=total,
                    effective_votes=float(total),
                    wins=tally['a'],
                    losses=tally['b'],
                    ties=tally['tie'],
                    both_bad=tally['both_bad'],
                    win_rate=sum(LEFT_SCORE[c] * n for c, n in tally.items()) / total,
                )
            )

    return rows


def _categories(
    categories: dict[str, str],
    anchor: str,
    votes: list[MatchedVote],
    comparisons: list[ranking.Comparison],
    outputs: list[Output],
    systems: list[str],
) -> list[CategoryBoard]:
    """Refit each category whose own root connects it to the board's anchor."""
    category_votes: defaultdict[str, list[MatchedVote]] = defaultdict(list)
    for m in votes:
        category_votes[categories[m.vote.case]].append(m)

    by_category: defaultdict[str, list[ranking.Comparison]] = defaultdict(list)
    for comparison in comparisons:
        by_category[categories[comparison.case]].append(comparison)

    entrants: defaultdict[str, set[str]] = defaultdict(set)
    for output in outputs:
        entrants[categories[output.case]].add(output.system)

    boards: list[CategoryBoard] = []
    for name, category_comparisons in sorted(by_category.items()):
        matchups = ranking.Matchups.build(category_comparisons)
        if anchor not in matchups.systems:
            continue

        matchups = matchups.component(anchor)
        ratings = ranking.rate_point(matchups, matchups.root(systems))
        ratings = ratings.anchored(anchor)

        standings: list[CategoryStanding] = []
        for i, system in enumerate(ratings.systems):
            mine = [m.vote for m in category_votes[name] if system in m.vote.systems]
            decisive = sum(vote.choice in ('a', 'b') for vote in mine)
            standings.append(
                CategoryStanding(
                    system=system,
                    score=float(ratings.score[i]),
                    interval=None,
                    votes=len(mine),
                    effective_votes=float(len(mine)),
                    decisive_votes=decisive,
                )
            )

        boards.append(
            CategoryBoard(
                category=name,
                votes=len(category_votes[name]),
                effective_votes=float(len(category_votes[name])),
                standings=standings,
                unranked=sorted(entrants[name] - set(ratings.systems)),
            )
        )

    return boards


def _length_feature(votes: list[MatchedVote]) -> ranking.Floats | None:
    """Return the z-scored (A − B) / (A + B) of each vote's output lengths.

    Returns None when every vote has the same relative length difference.
    """
    a = np.array([text_length(m.a.text or '') for m in votes])
    b = np.array([text_length(m.b.text or '') for m in votes])
    total = a + b
    relative = np.divide(a - b, total, out=np.zeros(len(votes)), where=total > 0)
    if not votes or np.ptp(relative) == 0:
        return None

    feature: ranking.Floats = (relative - relative.mean()) / relative.std()
    return feature


def text_length(text: str) -> int:
    """Count each Han character and each other whitespace-separated run as one.

    This measures Chinese in characters and English in words, including a text
    that mixes both. Han characters are the ones the language gate counts.
    """
    return len(_LENGTH_UNIT.findall(text))


def _split_half_tau(
    votes: list[MatchedVote], sessions: list[str], root: str
) -> float | None:
    """Compare the systems both halves connect to the root, if each has it.

    `sessions` lists the sessions holding `votes` in the order they started, and
    alternate sessions form each half.
    """
    parity = {session: i % 2 for i, session in enumerate(sessions)}
    halves = [
        [m.vote.comparison() for m in votes if parity[m.vote.session] == half]
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


def _rater_agreement(votes: list[Vote]) -> RaterAgreement:
    originals = {vote.id: vote for vote in votes}
    matches = [
        vote.choice == SWAPPED[originals[vote.repeat_of].choice]
        for vote in votes
        if vote.repeat_of in originals
    ]
    agreement = _share(matches)

    return RaterAgreement(
        repeats=len(matches),
        agreement=agreement,
        below_floor=agreement is not None and agreement < RATER_AGREEMENT_FLOOR,
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
