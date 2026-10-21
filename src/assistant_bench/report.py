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
from typing import Annotated, Literal, assert_never, get_args

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from assistant_bench.arena.resolution import MatchedVote, reconcile, settled
from assistant_bench.arena.store import Choice, Flag, Vote, VoteStore
from assistant_bench.families import (
    Family,
    TaskTestsSettings,
    VotesSettings,
    load_cases,
)
from assistant_bench.outputs import Output, read_outputs
from assistant_bench.paths import REPORTS_DIR
from assistant_bench.scoring import ranking


PRELIMINARY_DECISIVE_VOTES = 40
RATER_AGREEMENT_FLOOR = 0.7

# A Han ideograph, or a run of anything else up to whitespace or a Han ideograph.
_LENGTH_UNIT = re.compile(
    r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]'
    r'|[^\s\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]+'
)
_SWAPPED: dict[Choice, Choice] = {
    'a': 'b',
    'b': 'a',
    'tie': 'tie',
    'both_bad': 'both_bad',
}


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Interval(_Model):
    """A 95% bootstrap interval on the score scale."""

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
    its interval allows. `preliminary` marks fewer than 40 decisive votes. Flags
    count against the side a vote did not prefer, so a tie or both-bad vote
    counts them against both.
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
    flags: dict[Flag, int]


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
    """A refit on one category's votes, anchored at the same reference.

    `unranked` lists the systems voted on in the category that have no score, for
    the same reasons as on the board.
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
    or now fail a gate, and on pairs that the outputs now settle. `unranked` lists
    the voted systems that no comparison connects to the reference, or that so
    many resamples leave unconnected that their interval is unbounded. `position` is
    the left pane's advantage, and `length` the score change per standard
    deviation of the relative length difference, both in score points.
    `split_half_tau` compares the orders fit to odd and even sessions, and
    `decision` is absent while a system tied with the top one is preliminary.
    """

    scorer: Literal['votes']
    reference: str
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


def close(
    family: Family,
    store: VoteStore,
    closed_at: datetime,
) -> Report:
    """Aggregate the family's outputs, and its votes when votes score it.

    Raises
    ------
    ValueError
        The family has nothing to report, or a system has several fingerprints.
    """
    outputs = read_outputs(family.name)
    results: Results
    match family.settings:
        case VotesSettings():
            results = _votes_results(family, family.settings, store, outputs)
        case TaskTestsSettings():
            results = _tests_results(family, outputs)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{report.model_dump_json(indent=2)}\n')
    return path


# ── Votes Results ──


def _votes_results(
    family: Family,
    settings: VotesSettings,
    store: VoteStore,
    outputs: list[Output],
) -> VotesResults:
    """Fit the current case set, where votes and outputs on retired cases drop out.

    The systems with recorded votes enter the board, through the votes that
    `reconcile` keeps and the results their current outputs settle.
    """
    categories = {case.id: case.category for case in load_cases(family)}
    outputs = [output for output in outputs if output.case in categories]
    recorded = store.votes(family.name)
    if not recorded:
        raise ValueError(f'family {family.name!r} has no votes')
    votes = reconcile(recorded, outputs)
    voted = {side.system for vote in recorded for side in (vote.a, vote.b)}
    comparisons = [m.vote.comparison() for m in votes] + [
        comparison
        for comparison in settled(outputs)
        if {comparison.left, comparison.right} <= voted
    ]
    everything = ranking.Matchups.build(comparisons)
    if settings.reference not in everything.systems:
        dropped = sum(
            settings.reference in (vote.a.system, vote.b.system) for vote in recorded
        )
        reason = f'on its current outputs ({dropped} dropped)' if dropped else ''
        raise ValueError(
            f'family {family.name!r}: reference {settings.reference!r} '
            f'has no votes {reason}'.rstrip()
        )

    ratings = ranking.rate(
        everything.component(settings.reference), settings.reference
    ).bounded()
    ranked = set(ratings.systems)
    best, worst = ranking.rank_spread(ratings.lower, ratings.upper)
    connected = [m for m in votes if _systems([m]) <= ranked]
    length = (
        ranking.fit(
            ranking.Matchups.build([m.vote.comparison() for m in connected]),
            _length_feature(connected),
        ).covariate
        if connected
        else None
    )

    standings = [
        _standing(
            system,
            float(ratings.score[i]),
            Interval(lower=float(ratings.lower[i]), upper=float(ratings.upper[i])),
            int((ratings.score > ratings.score[i]).sum()) + 1,
            (int(best[i]), int(worst[i])),
            [m.vote for m in votes if system in _systems([m])],
            [output for output in outputs if output.system == system],
        )
        for i, system in enumerate(ratings.systems)
    ]
    standings.sort(key=lambda standing: (standing.rank, standing.system))
    sessions = [session.id for session in store.sessions(family.name)]
    return VotesResults(
        scorer='votes',
        reference=settings.reference,
        votes=len(votes),
        dropped_votes=len(recorded) - len(votes),
        sessions=len({m.vote.session for m in votes}),
        standings=standings,
        unranked=sorted(voted - ranked),
        head_to_head=_head_to_head([m.vote for m in votes], sorted(voted)),
        categories=_categories(categories, settings.reference, votes, comparisons),
        position=ratings.position,
        length=None if length is None else ranking.ELO_PER_LOGIT * length,
        separability=ranking.separability(ratings.lower, ratings.upper),
        split_half_tau=_split_half_tau(votes, sessions, settings.reference),
        rater_agreement=_rater_agreement(recorded),
        decision=decide(standings, ratings),
    )


def _systems(votes: Iterable[MatchedVote]) -> set[str]:
    return {side.system for m in votes for side in (m.vote.a, m.vote.b)}


def _standing(
    system: str,
    score: float,
    interval: Interval,
    rank: int,
    spread: tuple[int, int],
    votes: list[Vote],
    outputs: list[Output],
) -> Standing:
    fingerprints = {output.fingerprint for output in outputs}
    if len(fingerprints) > 1:
        raise ValueError(f'system {system!r} has outputs under several fingerprints')
    choices = Counter(vote.choice for vote in votes)
    flags = Counter(
        flag for vote in votes for flag in vote.flags if _flagged(vote, system)
    )
    finished = [output for output in outputs if output.error is None]
    gated = [output.gate_failures for output in finished]
    decisive = choices['a'] + choices['b']
    return Standing(
        system=system,
        fingerprint=fingerprints.pop(),
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
        gate_failure_rate=_share(bool(gates) for gates in gated if gates is not None),
        mean_cost_usd=_mean(output.cost_usd for output in finished),
        p50_latency_sec=_median(output.latency_sec for output in finished),
        output_tokens=_distribution(output.output_tokens for output in finished),
        latency_sec=_distribution(output.latency_sec for output in finished),
        flags={flag: flags[flag] for flag in get_args(Flag.__value__)},
    )


def _flagged(vote: Vote, system: str) -> bool:
    match vote.choice:
        case 'a':
            return vote.b.system == system
        case 'b':
            return vote.a.system == system
        case 'tie' | 'both_bad':
            return True
        case _:
            assert_never(vote.choice)


def _head_to_head(votes: list[Vote], systems: Iterable[str]) -> list[HeadToHead]:
    tallies: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for vote in votes:
        left, right = vote.a.system, vote.b.system
        tallies[(left, right)][vote.choice] += 1
        tallies[(right, left)][_SWAPPED[vote.choice]] += 1
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
                    win_rate=(tally['a'] + (tally['tie'] + tally['both_bad']) / 2)
                    / total,
                )
            )
    return rows


def _categories(
    category: dict[str, str],
    reference: str,
    votes: list[MatchedVote],
    comparisons: list[ranking.Comparison],
) -> list[CategoryBoard]:
    """Refit each category whose votes include the reference system."""
    by_category: defaultdict[str, list[MatchedVote]] = defaultdict(list)
    for m in votes:
        by_category[category[m.vote.case]].append(m)

    boards = []
    for name, category_votes in sorted(by_category.items()):
        voted = _systems(category_votes)
        if reference not in voted:
            continue
        matchups = ranking.Matchups.build(
            [c for c in comparisons if category[c.case] == name]
        ).component(reference)
        ratings = ranking.rate(matchups, reference).bounded()
        standings = []
        for i, system in enumerate(ratings.systems):
            mine = [m.vote for m in category_votes if system in _systems([m])]
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
                unranked=sorted(voted - set(ratings.systems)),
            )
        )
    return boards


def _length_feature(votes: list[MatchedVote]) -> ranking.Floats:
    """Return the z-scored (A − B) / (A + B) of each vote's output lengths."""
    a = np.array([text_length(m.a.text or '') for m in votes])
    b = np.array([text_length(m.b.text or '') for m in votes])
    total = a + b
    relative = np.divide(a - b, total, out=np.zeros(len(votes)), where=total > 0)
    centered = relative - relative.mean()
    spread = relative.std()
    feature: ranking.Floats = centered / spread if spread > 0 else centered
    return feature


def text_length(text: str) -> int:
    """Count each Han character and each other whitespace-separated run as one.

    This measures Chinese in characters and English in words, including a text
    that mixes both.
    """
    return len(_LENGTH_UNIT.findall(text))


def _split_half_tau(
    votes: list[MatchedVote], sessions: list[str], reference: str
) -> float | None:
    """Compare the systems both halves connect to the reference, if each has it."""
    parity = {session: i % 2 for i, session in enumerate(sessions)}
    halves = [
        [m.vote.comparison() for m in votes if parity[m.vote.session] == half]
        for half in (0, 1)
    ]
    if not all(any(reference in (c.left, c.right) for c in half) for half in halves):
        return None
    first, second = (
        dict(zip(fitted.systems, ranking.fit(fitted).strength, strict=True))
        for fitted in (
            ranking.Matchups.build(half).component(reference) for half in halves
        )
    )
    shared = sorted(first.keys() & second.keys())
    return ranking.rank_agreement(
        np.array([first[s] for s in shared]), np.array([second[s] for s in shared])
    )


def _rater_agreement(votes: list[Vote]) -> RaterAgreement:
    originals = {vote.id: vote for vote in votes}
    matches = [
        vote.choice == _SWAPPED[originals[vote.repeat_of].choice]
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


def _tests_results(family: Family, outputs: list[Output]) -> TestsResults:
    if not outputs:
        raise ValueError(f'family {family.name!r} has no outputs')
    by_system: defaultdict[str, list[Output]] = defaultdict(list)
    for output in outputs:
        by_system[output.system].append(output)
    standings = []
    for system, trials in sorted(by_system.items()):
        fingerprints = {output.fingerprint for output in trials}
        if len(fingerprints) > 1:
            raise ValueError(
                f'system {system!r} has outputs under several fingerprints'
            )
        standings.append(
            TestsStanding(
                system=system,
                fingerprint=fingerprints.pop(),
                trials=len(trials),
                errored=sum(output.error is not None for output in trials),
                pass_rate=sum(output.reward or 0.0 for output in trials) / len(trials),
                mean_cost_usd=_mean(output.cost_usd for output in trials),
                p50_latency_sec=_median(output.latency_sec for output in trials),
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
