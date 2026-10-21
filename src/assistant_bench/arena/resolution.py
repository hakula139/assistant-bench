"""Results that the outputs settle without a vote: gate failures and identical text."""

from collections.abc import Iterable
from itertools import combinations

from assistant_bench.outputs import Output
from assistant_bench.scoring.ranking import Comparison


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
    of a vote, so a case weighs at most one vote per pair of systems. Half of the
    weight sits on each side, which keeps these results out of the position term.
    """
    by_case: dict[str, dict[str, list[Output]]] = {}
    for output in outputs:
        if output.gate_failures is not None:
            by_system = by_case.setdefault(output.case, {})
            by_system.setdefault(output.system, []).append(output)

    comparisons = []
    for case, by_system in sorted(by_case.items()):
        for x, y in combinations(sorted(by_system), 2):
            share = 1 / (len(by_system[x]) * len(by_system[y])) / 2
            for a in by_system[x]:
                for b in by_system[y]:
                    score = settled_score(a, b)
                    if score is not None:
                        comparisons.append(Comparison(x, y, score, case, share))
                        comparisons.append(Comparison(y, x, 1 - score, case, share))
    return comparisons
