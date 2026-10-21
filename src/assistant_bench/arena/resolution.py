"""How votes and the current outputs combine: settled results and reconciled votes."""

from collections.abc import Iterable
from itertools import combinations
from typing import NamedTuple

from assistant_bench.arena.store import Side, Vote
from assistant_bench.outputs import Output
from assistant_bench.scoring.ranking import Comparison


class MatchedVote(NamedTuple):
    """A vote with the current outputs it compared, `a` shown on the left."""

    vote: Vote
    a: Output
    b: Output


def settled_score(a: Output, b: Output) -> float | None:
    """Return A's result when the outputs alone decide it, or `None` for a vote.

    An output that fails its gates loses, both failing count as both bad, and
    identical outputs tie.
    """
    match bool(a.gate_failures), bool(b.gate_failures):
        case True, True:
            return 0.5
        case True, False:
            return 0.0
        case False, True:
            return 1.0
    return 0.5 if a.text == b.text else None


def settled(outputs: Iterable[Output]) -> list[Comparison]:
    """Return the comparisons the gated outputs decide, derived afresh on each call.

    Each pairing of two systems' samples on a case counts 1 / (samples × samples)
    of a vote, so a case weighs at most one vote per pair of systems. No pane
    showed either side, so the results carry no side.
    """
    by_case: dict[str, dict[str, list[Output]]] = {}
    for output in outputs:
        if output.gate_failures is not None:
            by_system = by_case.setdefault(output.case, {})
            by_system.setdefault(output.system, []).append(output)

    comparisons = []
    for case, by_system in sorted(by_case.items()):
        for x, y in combinations(sorted(by_system), 2):
            share = 1 / (len(by_system[x]) * len(by_system[y]))
            for a in by_system[x]:
                for b in by_system[y]:
                    score = settled_score(a, b)
                    if score is not None:
                        comparisons.append(
                            Comparison(x, y, score, case, share, sided=False)
                        )
    return comparisons


def reconcile(votes: Iterable[Vote], outputs: Iterable[Output]) -> list[MatchedVote]:
    """Map each vote to the outputs it compared, among the current `outputs`.

    A vote drops out when either output has left `outputs` or no longer passes
    every gate, and when the two outputs now settle the pair, whose result from
    `settled` stands in for it.
    """
    current = {
        Side.of(output): output for output in outputs if output.gate_failures == []
    }
    matched = []
    for vote in votes:
        a, b = current.get(vote.a), current.get(vote.b)
        if a is not None and b is not None and settled_score(a, b) is None:
            matched.append(MatchedVote(vote, a, b))
    return matched
