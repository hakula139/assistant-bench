"""Balanced and adaptive draws from frozen inventory and current raw coverage."""

import math
import random
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import permutations, product

from assistant_bench_arena.ledger_models import Output, PointSnapshot
from assistant_bench_arena.read_models import ClaimState, Coverage, DueRepeat
from assistant_bench_arena.releases import EvaluationBundle
from assistant_bench_arena.serialization import comparison_identity


SEED_VOTES_PER_SIDE = 2
REPEAT_SHARE = 0.1


@dataclass(frozen=True)
class Draw:
    case_key: str
    a_output: str
    b_output: str
    claim_id: str | None = None
    claim_version: int = 0
    repeat_of: int | None = None


@dataclass(frozen=True)
class CandidatePool:
    ordinary: dict[tuple[str, str], list[Draw]]
    repeats: tuple[Draw, ...]

    @classmethod
    def build(
        cls,
        evaluation: EvaluationBundle,
        claims: Sequence[ClaimState],
        used_cases: Collection[str],
        repeats: Sequence[DueRepeat],
    ) -> CandidatePool:
        ordinary = {
            claim.comparison_key: claim for claim in claims if claim.kind == 'ordinary'
        }
        by_case: defaultdict[str, defaultdict[str, list[Output]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for output in evaluation.outputs:
            if output.verdict == 'pass' and output.case_key not in used_cases:
                by_case[output.case_key][output.system_id].append(output)

        candidates: defaultdict[tuple[str, str], list[Draw]] = defaultdict(list)

        for case, systems in sorted(by_case.items()):
            for left, right in permutations(sorted(systems), 2):
                claim = ordinary.get(comparison_identity(case, left, right))

                if claim is not None and claim.eligible:
                    continue

                for a, b in product(systems[left], systems[right]):
                    if a.content_digest != b.content_digest:
                        candidates[left, right].append(
                            Draw(
                                case,
                                a.key,
                                b.key,
                                claim.id if claim is not None else None,
                                claim.version if claim is not None else 0,
                            )
                        )

        due = tuple(
            Draw(
                repeat.case_key,
                repeat.a_output,
                repeat.b_output,
                repeat.claim_id,
                repeat.claim_version or 0,
                repeat.receipt_seq,
            )
            for repeat in repeats
            if repeat.case_key not in used_cases
        )
        return cls(dict(candidates), due)

    @property
    def case_keys(self) -> frozenset[str]:
        cases = {
            candidate.case_key
            for candidates in self.ordinary.values()
            for candidate in candidates
        }
        return frozenset(cases | {repeat.case_key for repeat in self.repeats})

    @property
    def remaining(self) -> int:
        return len(self.case_keys)

    def draw(
        self,
        points: PointSnapshot,
        coverage: Sequence[Coverage],
        points_per_logit: float,
        rng: random.Random,
    ) -> Draw | None:
        if self.repeats and (not self.ordinary or rng.random() < REPEAT_SHARE):
            return rng.choice(self.repeats)

        if not self.ordinary:
            return None

        counts = {(row.a_system, row.b_system): row.count for row in coverage}
        short = [
            pair for pair in self.ordinary if counts.get(pair, 0) < SEED_VOTES_PER_SIDE
        ]

        if short:
            minimum = min(counts.get(pair, 0) for pair in short)
            pair = rng.choice(
                [pair for pair in short if counts.get(pair, 0) == minimum]
            )
        else:
            pairs = sorted({(min(pair), max(pair)) for pair in self.ordinary})
            log_weights = pair_log_weights(points, pairs, counts, points_per_logit)
            largest = max(log_weights)
            weights = [math.exp(weight - largest) for weight in log_weights]
            left, right = rng.choices(pairs, weights=weights, k=1)[0]
            orientations = [
                pair for pair in ((left, right), (right, left)) if pair in self.ordinary
            ]
            minimum = min(counts.get(pair, 0) for pair in orientations)
            pair = rng.choice(
                [pair for pair in orientations if counts.get(pair, 0) == minimum]
            )

        cases = sorted({candidate.case_key for candidate in self.ordinary[pair]})
        case = rng.choice(cases)
        return rng.choice(
            [
                candidate
                for candidate in self.ordinary[pair]
                if candidate.case_key == case
            ]
        )


def pair_log_weights(
    points: PointSnapshot,
    pairs: Sequence[tuple[str, str]],
    counts: dict[tuple[str, str], int],
    points_per_logit: float,
) -> list[float]:
    gaps: list[Fraction] = []

    for left, right in pairs:
        a, b = points.points[left], points.points[right]
        gap = Fraction(0)

        if a.score is not None and b.score is not None and a.component == b.component:
            gap = abs(Fraction(a.score) - Fraction(b.score)) / Fraction(
                points_per_logit
            )

        gaps.append(gap)

    smallest = min(gaps)
    weights: list[float] = []

    for (left, right), gap in zip(pairs, gaps, strict=True):
        information = -_float_gap(gap - smallest) - 2 * math.log1p(
            math.exp(-_float_gap(gap))
        )
        weights.append(
            information
            - math.log(counts.get((left, right), 0) + counts.get((right, left), 0) + 1)
        )

    return weights


def _float_gap(gap: Fraction) -> float:
    try:
        return float(gap)
    except OverflowError:
        return math.inf
