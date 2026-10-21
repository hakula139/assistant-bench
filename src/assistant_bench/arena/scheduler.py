"""Pair scheduling: a balanced seed, adaptive pairing, and delayed swapped repeats."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import permutations

import numpy as np
from scipy import special

from assistant_bench.arena.store import Side, Vote
from assistant_bench.outputs import Output
from assistant_bench.scoring.ranking import ELO_PER_LOGIT, Matchups, rate


SEED_VOTES_PER_SIDE = 2
REPEAT_SHARE = 0.1
REPEAT_DELAY = timedelta(days=1)


@dataclass(frozen=True)
class Pool:
    """A family's gated outputs by case and system, for the systems it lists."""

    systems: tuple[str, ...]
    outputs_by_case: dict[str, dict[str, list[Output]]]

    @classmethod
    def build(cls, systems: Iterable[str], outputs: Iterable[Output]) -> Pool:
        """Keep every output of a listed system that the gates checked."""
        listed = tuple(systems)
        outputs_by_case: dict[str, dict[str, list[Output]]] = {}
        for output in outputs:
            if output.system in listed and output.gate_failures is not None:
                by_system = outputs_by_case.setdefault(output.case, {})
                by_system.setdefault(output.system, []).append(output)
        return cls(systems=listed, outputs_by_case=outputs_by_case)

    def cases(self, left: str, right: str) -> list[str]:
        """Return the cases with outputs from both systems."""
        return sorted(
            case
            for case, by_system in self.outputs_by_case.items()
            if left in by_system and right in by_system
        )

    def paired_cases(self) -> set[str]:
        """Return the cases on which at least two systems have outputs."""
        return {
            case
            for case, by_system in self.outputs_by_case.items()
            if len(by_system) > 1
        }

    def find(self, case: str, side: Side) -> Output | None:
        for output in self.outputs_by_case.get(case, {}).get(side.system, []):
            if Side.of(output) == side:
                return output
        return None


@dataclass(frozen=True)
class Draw:
    """A pair to show, with side A on the left."""

    case: str
    a: Output
    b: Output
    repeat_of: int | None = None


def draw(
    pool: Pool,
    votes: list[Vote],
    used: set[str],
    now: datetime,
    rng: np.random.Generator,
) -> Draw | None:
    """Choose the next pair on a case the session has not used.

    A delayed repeat comes first, at `REPEAT_SHARE` odds while one is due. Then
    every ordered pair of systems collects `SEED_VOTES_PER_SIDE` votes, and after
    that the scheduler favors close pairs with few votes.
    """
    repeats = _due_repeats(pool, votes, used, now)
    if repeats and rng.random() < REPEAT_SHARE:
        return repeats[rng.integers(len(repeats))]

    open_cases = {
        pair: [case for case in pool.cases(*pair) if case not in used]
        for pair in permutations(pool.systems, 2)
    }
    open_cases = {pair: cases for pair, cases in open_cases.items() if cases}
    if not open_cases:
        return None
    counts = Counter((vote.a.system, vote.b.system) for vote in votes)
    pair = _seed_pair(open_cases, counts, rng) or _adaptive_pair(
        open_cases, counts, votes, rng
    )
    left, right = pair
    case = open_cases[pair][rng.integers(len(open_cases[pair]))]
    by_system = pool.outputs_by_case[case]
    return Draw(
        case=case,
        a=by_system[left][rng.integers(len(by_system[left]))],
        b=by_system[right][rng.integers(len(by_system[right]))],
    )


def _due_repeats(
    pool: Pool, votes: list[Vote], used: set[str], now: datetime
) -> list[Draw]:
    repeated = {vote.repeat_of for vote in votes}
    due = []
    for vote in votes:
        if (
            vote.auto is None
            and vote.repeat_of is None
            and vote.id not in repeated
            and vote.case not in used
            and now - vote.recorded_at >= REPEAT_DELAY
        ):
            a = pool.find(vote.case, vote.b)
            b = pool.find(vote.case, vote.a)
            if a is not None and b is not None:
                due.append(Draw(case=vote.case, a=a, b=b, repeat_of=vote.id))
    return due


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
    votes: list[Vote],
    rng: np.random.Generator,
) -> tuple[str, str]:
    """Weight each pair by p(1 - p) / (n + 1), among pairs whose intervals overlap.

    The weight favors pairs that are close and have few votes. A board whose
    intervals all separate weighs every pair the same way.
    """
    matchups = Matchups.build([vote.comparison() for vote in votes])
    ratings = rate(matchups, matchups.systems[0])
    index = {system: i for i, system in enumerate(ratings.systems)}

    unordered = sorted({tuple(sorted(pair)) for pair in open_cases})
    overlapping = [
        (x, y)
        for x, y in unordered
        if ratings.lower[index[x]] <= ratings.upper[index[y]]
        and ratings.lower[index[y]] <= ratings.upper[index[x]]
    ]
    pairs = overlapping or unordered
    gap = np.array(
        [ratings.score[index[x]] - ratings.score[index[y]] for x, y in pairs]
    )
    p = special.expit(gap / ELO_PER_LOGIT)
    n = np.array([counts[(x, y)] + counts[(y, x)] for x, y in pairs])
    weights = p * (1 - p) / (n + 1)
    x, y = pairs[rng.choice(len(pairs), p=weights / weights.sum())]

    sides = [pair for pair in ((x, y), (y, x)) if pair in open_cases]
    fewest = min(counts[pair] for pair in sides)
    sides = [pair for pair in sides if counts[pair] == fewest]
    return sides[rng.integers(len(sides))]
