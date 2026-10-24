"""The report schema, with one results variant per scorer, and the writer of rounds.

A report holds aggregates under public system names and nothing else: no case,
instruction, output text, route, or request ID.
"""

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, assert_never

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from zhon import hanzi

from assistant_bench.arena.resolution import MatchedVote, reconcile, settled
from assistant_bench.arena.store import (
    LEFT_SCORE,
    SWAPPED,
    Choice,
    Vote,
    VoteStore,
)
from assistant_bench.families import (
    Defect,
    Family,
    TaskTestsSettings,
    VotesSettings,
    load_cases,
)
from assistant_bench.outputs import Output, read_outputs
from assistant_bench.paths import REPORTS_DIR, write_atomically
from assistant_bench.scoring import ranking


PRELIMINARY_DECISIVE_VOTES = 40
RATER_AGREEMENT_FLOOR = 0.7

# A Han character, or a run of anything else up to whitespace or a Han character.
_LENGTH_UNIT = re.compile(f'[{hanzi.characters}]|[^\\s{hanzi.characters}]+')


# ── Shared Schema ──


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Interval(_Model):
    """A bootstrap interval at `ranking.CONFIDENCE` on the score scale."""

    lower: float
    upper: float


class Distribution(_Model):
    p10: float
    p50: float
    p90: float


# ── Votes Schema ──


class Standing(_Model):
    """One system's row on a board scored by votes.

    `rank` orders the scores, while `best_rank` and `worst_rank` span the ranks
    its interval allows. `preliminary` marks fewer than `PRELIMINARY_DECISIVE_VOTES`
    decisive votes. `outputs` counts the outputs that finished without an
    error, while `gate_failure_rate` covers every output with a verdict,
    including a task's solution whose agent errored but whose tests still ran.
    `defects` counts the votes that tagged this system's output with each of
    the family's defects, in the family's order, whichever side won.
    """

    system: str
    fingerprint: str | None
    score: float
    interval: Interval
    rank: int
    best_rank: int
    worst_rank: int
    preliminary: bool
    votes: int
    decisive_votes: int
    both_bad_rate: float | None
    outputs: int
    gate_failure_rate: float | None
    mean_cost_usd: float | None
    p50_latency_sec: float | None
    output_tokens: Distribution | None
    latency_sec: Distribution | None
    defects: dict[Defect, int]


class HeadToHead(_Model):
    """Votes between two systems, where ties and both-bad votes count half."""

    system: str
    opponent: str
    votes: int
    wins: int
    losses: int
    ties: int
    both_bad: int
    win_rate: float


class CategoryStanding(_Model):
    system: str
    score: float
    interval: Interval
    votes: int
    preliminary: bool


class CategoryBoard(_Model):
    """A refit on one category's votes, anchored at the same system as the board.

    `unranked` lists the systems with outputs in the category that have no score
    there, for the same reasons as on the board.
    """

    category: str
    votes: int
    standings: list[CategoryStanding]
    unranked: list[str]


class RaterAgreement(_Model):
    """How often a side-swapped repeat matched the original vote."""

    repeats: int
    agreement: float | None
    below_floor: bool


class Decision(_Model):
    """The leader among the systems tied with the top-scoring one, and what chose it.

    `separated` means no other ranked system ties with the top-scoring one, and
    `tie_break` is then `score`. Otherwise `cost` or `latency` broke the tie by
    the lower mean cost or p50 latency, where a known value beats a missing one,
    and `score` remains the last resort.
    """

    leader: str
    separated: bool
    tie_break: Literal['score', 'cost', 'latency']


class VotesResults(_Model):
    """A board fit to the family's votes and the results its outputs settle.

    `votes` counts the votes that compare current outputs needing a vote, while
    `dropped_votes` counts the rest: votes on retired cases, on outputs that left
    or now fail a gate, and on pairs that the outputs now settle. `sessions`
    counts the sessions that hold a counted vote. `reference` is the system the
    family names to anchor the board, while `anchor` is the system the round
    actually fixed at `ranking.ANCHOR`, which is the most-voted system when the
    reference does not rank. `unranked` lists the listed or voted systems with
    current outputs that no comparison connects to the anchor, or that so many
    resamples leave unconnected that their interval is unbounded. `position` is
    the left pane's advantage, and `length` the score change per standard
    deviation of the relative length difference, both in score points, where
    `length` is absent when no connected vote varies that difference.
    `split_half_tau` compares the orders fit to odd and even sessions, and
    `decision` is absent while a system tied with the top one is preliminary.
    """

    scorer: Literal['votes']
    reference: str
    anchor: str
    votes: int
    dropped_votes: int
    sessions: int
    standings: list[Standing]
    unranked: list[str]
    head_to_head: list[HeadToHead]
    categories: list[CategoryBoard]
    position: float
    length: float | None
    separability: float | None
    split_half_tau: float | None
    rater_agreement: RaterAgreement
    decision: Decision | None


# ── Tests Schema ──


class TestsStanding(_Model):
    """One system's pass rate, with cost and latency over its error-free trials."""

    system: str
    fingerprint: str | None
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
    closed_at: datetime
    results: Results


def close(family: Family, store: VoteStore, closed_at: datetime) -> Report:
    """Aggregate the family's current outputs, and its votes when votes score it.

    Raises
    ------
    ValueError
        The family has nothing to report, or a system has several fingerprints.
    """
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
            results = _votes_results(
                family, family.settings, store, outputs, categories
            )
        case TaskTestsSettings():
            results = _tests_results(outputs)
        case _:
            assert_never(family.settings)
    return Report(
        family=family.name,
        round=closed_at.date().isoformat(),
        closed_at=closed_at,
        results=results,
    )


def write_report(report: Report) -> Path:
    path = REPORTS_DIR / report.family / f'{report.round}.json'
    write_atomically(path, f'{report.model_dump_json(indent=2)}\n')
    return path


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
    outputs: list[Output],
    categories: dict[str, str],
) -> VotesResults:
    """Fit the votes and settled results on the current cases' `outputs`.

    `categories` maps each current case to its category. Every listed system
    with current outputs enters the board, as does every system with recorded
    votes, through the votes that `reconcile` keeps and the results its graded
    outputs settle, so a system that the gates or a task's tests keep from
    every vote still ranks. An unlisted system the owner never voted on stays
    off the board, as the arena never pairs it.
    """
    recorded = store.votes(family.name)
    if not recorded:
        raise ValueError(
            f'Family `{family.name}` has no votes yet. Vote with '
            '`uv run bench serve`, then close the round.'
        )
    voted = {system for vote in recorded for system in vote.systems}
    entrants = set(settings.systems) | voted
    outputs = [output for output in outputs if output.system in entrants]
    votes = reconcile(recorded, outputs)
    if not votes:
        raise ValueError(
            f'Family `{family.name}` has votes, but none compares two of its '
            'current outputs. Vote on the current outputs, then close the round.'
        )
    fingerprints = _fingerprints(outputs)
    comparisons = [m.vote.comparison() for m in votes] + settled(outputs)
    everything = ranking.Matchups.build(comparisons)
    root = everything.root(settings.systems)

    ratings = ranking.rate(everything.component(root), root).bounded()
    anchor = settings.reference if settings.reference in ratings.systems else root
    ratings = ratings.anchored(anchor)
    ranked = set(ratings.systems)
    best, worst = ranking.rank_spread(ratings.lower, ratings.upper)
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
            fingerprints[system],
            float(ratings.score[i]),
            Interval(lower=float(ratings.lower[i]), upper=float(ratings.upper[i])),
            int((ratings.score > ratings.score[i]).sum()) + 1,
            (int(best[i]), int(worst[i])),
            [m.vote for m in votes if system in m.vote.systems],
            [output for output in outputs if output.system == system],
            settings.defects,
        )
        for i, system in enumerate(ratings.systems)
    ]
    standings.sort(key=lambda standing: (standing.rank, standing.system))
    counted = {m.vote.session for m in votes}
    sessions = [s.id for s in store.sessions(family.name) if s.id in counted]
    return VotesResults(
        scorer='votes',
        reference=settings.reference,
        anchor=anchor,
        votes=len(votes),
        dropped_votes=len(recorded) - len(votes),
        sessions=len(sessions),
        standings=standings,
        unranked=sorted(fingerprints.keys() - ranked),
        head_to_head=_head_to_head([m.vote for m in votes], sorted(voted)),
        categories=_categories(
            categories, anchor, votes, comparisons, outputs, settings.systems
        ),
        position=ratings.position,
        length=None if length is None else ranking.ELO_PER_LOGIT * length,
        separability=ranking.separability(ratings.lower, ratings.upper),
        split_half_tau=_split_half_tau(votes, sessions, root),
        rater_agreement=_rater_agreement(recorded),
        decision=decide(standings, ratings),
    )


def _standing(
    system: str,
    fingerprint: str | None,
    score: float,
    interval: Interval,
    rank: int,
    spread: tuple[int, int],
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
        fingerprint=fingerprint,
        score=score,
        interval=interval,
        rank=rank,
        best_rank=spread[0],
        worst_rank=spread[1],
        preliminary=decisive < PRELIMINARY_DECISIVE_VOTES,
        votes=len(votes),
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
    rows = []
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
    by_category: defaultdict[str, list[MatchedVote]] = defaultdict(list)
    for m in votes:
        by_category[categories[m.vote.case]].append(m)
    entrants: defaultdict[str, set[str]] = defaultdict(set)
    for output in outputs:
        entrants[categories[output.case]].add(output.system)

    boards = []
    for name, category_votes in sorted(by_category.items()):
        matchups = ranking.Matchups.build(
            [c for c in comparisons if categories[c.case] == name]
        )
        if anchor not in matchups.systems:
            continue
        matchups = matchups.component(anchor)
        ratings = ranking.rate(matchups, matchups.root(systems)).bounded()
        if anchor not in ratings.systems:
            continue
        ratings = ratings.anchored(anchor)
        standings = []
        for i, system in enumerate(ratings.systems):
            mine = [m.vote for m in category_votes if system in m.vote.systems]
            decisive = sum(vote.choice in ('a', 'b') for vote in mine)
            standings.append(
                CategoryStanding(
                    system=system,
                    score=float(ratings.score[i]),
                    interval=Interval(
                        lower=float(ratings.lower[i]), upper=float(ratings.upper[i])
                    ),
                    votes=len(mine),
                    preliminary=decisive < PRELIMINARY_DECISIVE_VOTES,
                )
            )
        boards.append(
            CategoryBoard(
                category=name,
                votes=len(category_votes),
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


def decide(standings: list[Standing], ratings: ranking.Ratings) -> Decision | None:
    """Choose the leader among the systems that tie with the top-scoring one.

    A system ties when its paired bootstrap difference from the top has an
    interval containing 0. While a tied system is preliminary there is no
    decision, and otherwise the lower mean cost, then the lower p50 latency,
    then the higher score break the tie.
    """
    index = {system: i for i, system in enumerate(ratings.systems)}
    tied_with_top = set(ranking.ties(ratings, [index[s.system] for s in standings]))
    tied = [s for s in standings if index[s.system] in tied_with_top]
    if any(standing.preliminary for standing in tied):
        return None
    if len(tied) == 1:
        return Decision(leader=tied[0].system, separated=True, tie_break='score')
    cheapest = _lowest(tied, lambda standing: standing.mean_cost_usd)
    if len(cheapest) == 1:
        return Decision(leader=cheapest[0].system, separated=False, tie_break='cost')
    fastest = _lowest(cheapest, lambda standing: standing.p50_latency_sec)
    if len(fastest) == 1:
        return Decision(leader=fastest[0].system, separated=False, tie_break='latency')
    leader = min(fastest, key=lambda standing: standing.rank)
    return Decision(leader=leader.system, separated=False, tie_break='score')


def _lowest(
    standings: list[Standing], key: Callable[[Standing], float | None]
) -> list[Standing]:
    """Keep the standings with the lowest known value, or all when none is known."""
    known = [value for standing in standings if (value := key(standing)) is not None]
    if not known:
        return standings
    return [standing for standing in standings if key(standing) == min(known)]


# ── Tests Results ──


def _tests_results(outputs: list[Output]) -> TestsResults:
    fingerprints = _fingerprints(outputs)
    by_system: defaultdict[str, list[Output]] = defaultdict(list)
    for output in outputs:
        by_system[output.system].append(output)
    standings = []
    for system, trials in sorted(by_system.items()):
        finished = [output for output in trials if output.error is None]
        standings.append(
            TestsStanding(
                system=system,
                fingerprint=fingerprints[system],
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
