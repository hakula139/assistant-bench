"""Pair scheduling: a balanced seed, adaptive pairing, and delayed swapped repeats."""

from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations, permutations

import numpy as np
from scipy import special

from assistant_bench.arena.resolution import MatchedVote, settled, settled_score
from assistant_bench.outputs import Output
from assistant_bench.scoring.ranking import (
    ELO_PER_LOGIT,
    Comparison,
    Matchups,
    PointRatings,
    rate_point,
)


SEED_VOTES_PER_SIDE = 2
REPEAT_SHARE = 0.1
REPEAT_DELAY = timedelta(days=1)
REFIT_VOTES = 5


@dataclass(frozen=True)
class Pool:
    """The current outputs of a family's listed systems on its current cases."""

    systems: tuple[str, ...]
    outputs: list[Output]
    outputs_by_case: dict[str, dict[str, list[Output]]]
    """Outputs that pass every gate and can reach the owner."""

    settled: list[Comparison]
    """Results the outputs decide without a vote."""

    @classmethod
    def build(
        cls, systems: Iterable[str], cases: Collection[str], outputs: Iterable[Output]
    ) -> Pool:
        """Keep the outputs of the listed systems on the family's current cases."""
        listed = tuple(systems)
        current = [
            output
            for output in outputs
            if output.system in listed and output.case in cases
        ]

        outputs_by_case: dict[str, dict[str, list[Output]]] = {}
        for output in current:
            if output.gate_failures == []:
                by_system = outputs_by_case.setdefault(output.case, {})
                by_system.setdefault(output.system, []).append(output)

        return cls(listed, current, outputs_by_case, settled(current))

    def pairings(self, case: str, left: str, right: str) -> list[tuple[Output, Output]]:
        """Return the pairings of two systems' samples on a case that need a vote."""
        by_system = self.outputs_by_case.get(case, {})
        return [
            (a, b)
            for a in by_system.get(left, [])
            for b in by_system.get(right, [])
            if settled_score(a, b) is None
        ]

    def cases(self, left: str, right: str) -> list[str]:
        return sorted(
            case for case in self.outputs_by_case if self.pairings(case, left, right)
        )

    def votable_cases(self) -> set[str]:
        """Return the cases on which some pair of systems needs a vote."""
        return {
            case
            for case in self.outputs_by_case
            for left, right in combinations(self.systems, 2)
            if self.pairings(case, left, right)
        }


@dataclass(frozen=True)
class Draw:
    """A pair to show, with side A on the left."""

    case: str
    a: Output
    b: Output
    repeat_of: int | None = None


class Board:
    """A family's ratings for the adaptive draw, which reuses them between refits.

    The board refits when `REFIT_VOTES` votes have been cast or undone since the
    last point fit, when it lacks a system the draw weighs, or after `reset`.
    Each fit roots at the most-voted system, and `order`, the family's `systems`
    list, breaks a tie.
    """

    def __init__(self, order: Sequence[str]) -> None:
        self._order = tuple(order)
        self._ratings: PointRatings | None = None
        self._fitted: frozenset[int] = frozenset()

    def reset(self) -> None:
        """Refit on the next adaptive draw."""
        self._ratings = None

    def ratings(
        self,
        votes: list[MatchedVote],
        settled: list[Comparison],
        systems: Collection[str],
    ) -> PointRatings:
        """Return ratings that cover `systems`, refitting them when stale."""
        current = frozenset(m.vote.id for m in votes)
        if (
            self._ratings is None
            or len(self._fitted ^ current) >= REFIT_VOTES
            or not set(systems) <= set(self._ratings.systems)
        ):
            matchups = Matchups.build([m.vote.comparison() for m in votes] + settled)
            self._ratings = rate_point(matchups, matchups.root(self._order))
            self._fitted = current
        return self._ratings


def draw(
    pool: Pool,
    votes: list[MatchedVote],
    used: set[str],
    now: datetime,
    rng: np.random.Generator,
    board: Board,
) -> Draw | None:
    """Choose the next pair on a case the session has not used.

    `votes` are the family's votes reconciled with `pool.outputs`. A delayed
    repeat comes first, at `REPEAT_SHARE` odds while one is due. Then every
    ordered pair of systems collects `SEED_VOTES_PER_SIDE` votes, and after that
    the scheduler favors close pairs with few votes on `board`'s ratings.
    """
    repeats = _due_repeats(votes, used, now)
    if repeats and rng.random() < REPEAT_SHARE:
        return repeats[rng.integers(len(repeats))]

    shared = {
        frozenset(pair): pool.cases(*pair) for pair in combinations(pool.systems, 2)
    }

    open_cases = {
        pair: [case for case in shared[frozenset(pair)] if case not in used]
        for pair in permutations(pool.systems, 2)
    }
    open_cases = {pair: cases for pair, cases in open_cases.items() if cases}
    if not open_cases:
        return None

    counts = Counter(m.vote.systems for m in votes)
    pair = _seed_pair(open_cases, counts, rng) or _adaptive_pair(
        open_cases,
        counts,
        board.ratings(votes, pool.settled, {s for pair in open_cases for s in pair}),
        rng,
    )

    case = open_cases[pair][rng.integers(len(open_cases[pair]))]
    pairings = pool.pairings(case, *pair)
    a, b = pairings[rng.integers(len(pairings))]
    return Draw(case=case, a=a, b=b)


def pair_weights(
    ratings: PointRatings,
    pairs: list[tuple[str, str]],
    counts: Counter[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    """Weight each pair of systems by p(1 - p) / (n + 1).

    `p` is the fitted preference within the pair and `n` its votes in either
    order, so the weight favors pairs that are close and have few votes.
    """
    index = {system: i for i, system in enumerate(ratings.systems)}
    gap = np.array(
        [ratings.score[index[x]] - ratings.score[index[y]] for x, y in pairs]
    )
    logit = gap / ELO_PER_LOGIT
    n = np.array([counts[(x, y)] + counts[(y, x)] for x, y in pairs])
    weights = special.expit(logit) * special.expit(-logit) / (n + 1)
    return {pair: float(weight) for pair, weight in zip(pairs, weights, strict=True)}


def _due_repeats(votes: list[MatchedVote], used: set[str], now: datetime) -> list[Draw]:
    repeated = {m.vote.repeat_of for m in votes}
    return [
        Draw(case=m.vote.case, a=m.b, b=m.a, repeat_of=m.vote.id)
        for m in votes
        if m.vote.repeat_of is None
        and m.vote.id not in repeated
        and m.vote.case not in used
        and now - m.vote.recorded_at >= REPEAT_DELAY
    ]


def _seed_pair(
    open_cases: dict[tuple[str, str], list[str]],
    counts: Counter[tuple[str, str]],
    rng: np.random.Generator,
) -> tuple[str, str] | None:
    short = [pair for pair in open_cases if counts[pair] < SEED_VOTES_PER_SIDE]
    if not short:
        return None
    fewest = min(counts[pair] for pair in short)
    pairs = [pair for pair in short if counts[pair] == fewest]
    return pairs[rng.integers(len(pairs))]


def _adaptive_pair(
    open_cases: dict[tuple[str, str], list[str]],
    counts: Counter[tuple[str, str]],
    ratings: PointRatings,
    rng: np.random.Generator,
) -> tuple[str, str]:
    """Draw a pair by `pair_weights`, putting the order shown less often left."""
    unordered = sorted({(min(pair), max(pair)) for pair in open_cases})
    weights = pair_weights(ratings, unordered, counts)
    pairs = list(weights)
    total = np.array(list(weights.values()))
    x, y = pairs[rng.choice(len(pairs), p=total / total.sum())]

    sides = [pair for pair in ((x, y), (y, x)) if pair in open_cases]
    fewest = min(counts[pair] for pair in sides)
    sides = [pair for pair in sides if counts[pair] == fewest]
    return sides[rng.integers(len(sides))]
