"""Synthetic frozen evidence shared by offline report and snapshot tests."""

from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from itertools import combinations, product

import numpy as np

from assistant_bench.families import Category, Choice, Defect
from assistant_bench.report import (
    AnalysisInput,
    HumanEvidence,
    LedgerProvenance,
    OutputMeasurement,
)
from assistant_bench.scoring import ranking
from assistant_bench.scoring.ranking import Comparison
from assistant_bench.scoring.weighting import actor_weights
from support import PROSE_DEFECTS


def make_evidence(
    index: int,
    *,
    actor: str = 'owner',
    session: str = 'first',
    choice: Choice = 'a',
    systems: tuple[str, str] = ('a', 'b'),
    weight: float = 1.0,
    length_a: int = 10,
    length_b: int = 10,
) -> HumanEvidence:
    return HumanEvidence(
        receipt=f'receipt-{index}',
        actor=actor,
        session=session,
        choice=choice,
        defects_a=(),
        defects_b=(),
        length_a=length_a,
        length_b=length_b,
        comparison=Comparison(
            *systems, ranking.LEFT_SCORE[choice], f'case-{index}', weight
        ),
    )


def make_analysis(
    ordinary: tuple[HumanEvidence, ...] = (),
    *,
    settlements: tuple[Comparison, ...] = (),
    dropped: int = 0,
    categories: tuple[tuple[str, Category | None], ...] | None = None,
) -> AnalysisInput:
    comparisons = tuple(row.comparison for row in ordinary) + settlements
    resolved: tuple[tuple[str, Category | None], ...] = (
        tuple((row.case, 'docs') for row in comparisons)
        if categories is None
        else categories
    )
    outputs = tuple(
        OutputMeasurement(
            case=row.case,
            system=system,
            errored=False,
            verdict='pass',
            reward=None,
            cost_usd=0.01,
            latency_sec=2.0,
            output_tokens=20,
        )
        for row in comparisons
        for system in (row.left, row.right)
    )
    return AnalysisInput(
        family='f',
        reference='a',
        systems=('a', 'b', 'x', 'y', 'unrated'),
        defects=('facts',),
        provenance=LedgerProvenance(
            status='ledger',
            schema_version=1,
            export_id='export-1',
            export_digest='a' * 64,
            namespace='test-deployment',
            captured_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_cutoff=len(ordinary) + dropped + 10,
            receipt_cutoff=len(ordinary) + dropped,
            family_revision=4,
            analysis_sequence=len(ordinary) + dropped + 10,
            evaluation_id='evaluation-1',
            publication_id='publication-1',
            policy_id='b' * 64,
            sampling_point_snapshot_id='points-before',
            weighted_evidence_digest='c' * 64,
            fitted_point_snapshot_id='points-after',
        ),
        declared_categories=tuple(
            dict.fromkeys(category for _, category in resolved if category is not None)
        ),
        categories=resolved,
        ordinary=ordinary,
        settlements=settlements,
        outputs=outputs,
        sessions=tuple(dict.fromkeys(row.session for row in ordinary)),
        accepted=len(ordinary) + dropped,
    )


def make_capped_analysis() -> AnalysisInput:
    choices: tuple[Choice, ...] = ('a', 'a', 'a', 'both_bad', 'tie')
    ordinary = tuple(
        make_evidence(
            index,
            session=f'owner-{index % 2}',
            choice=choices[index % len(choices)],
            systems=('a', 'b') if index < 200 else ('x', 'y'),
            length_a=10 + index % 7,
            length_b=10,
        )
        for index in range(250)
    ) + tuple(
        make_evidence(
            250 + index,
            actor='visitor',
            session=f'visitor-{index % 2}',
            choice='b',
            length_a=9 + index % 5,
        )
        for index in range(25)
    )
    weights = actor_weights(('owner',) * 250 + ('visitor',) * 25)
    weighted = tuple(
        replace(row, comparison=row.comparison._replace(weight=weight))
        for row, weight in zip(ordinary, weights, strict=True)
    )
    categories: tuple[tuple[str, Category | None], ...] = tuple(
        (
            row.comparison.case,
            'review'
            if index in range(200, 250)
            else 'docs'
            if index < 10 or index in range(250, 255)
            else 'message',
        )
        for index, row in enumerate(weighted)
    )
    return make_analysis(weighted, dropped=1, categories=categories)


# ── Synthetic Round ──

ROUND_STRENGTHS = {
    'acme-large': 0.6,
    'acme-small': 0.2,
    'acme-pro': 0.5,
    'acme-flash': -0.1,
    'acme-mini': -0.4,
    'acme-edge': 0.3,
    'acme-nano': -0.2,
    'acme-tiny': -1.0,
}
"""Each system's Bradley-Terry strength, where the first five share the voted pool."""

ROUND_NAMES = {system: system.replace('-', ' ').title() for system in ROUND_STRENGTHS}
ROUND_CATEGORIES: tuple[Category, ...] = ('docs', 'message', 'reply')
OWNER_SESSIONS = 4


def make_round(sessions: int, captured_at: datetime) -> AnalysisInput:
    """Simulate family `arena` over `sessions` daily sessions of about 30 votes each.

    A seeded rater follows Bradley-Terry with the strengths above and a small left
    bias, ties 15% of the time and calls both bad 5%. The owner votes in the first
    four sessions, past the actor cap, and a new visitor in each later one. About 3%
    of the outputs fail a gate and settle their pairs without a vote, while
    `acme-tiny` fails most cases, so it ranks through settlements alone. Each session
    pits `acme-edge` against `acme-nano` on two cases, which leaves them unranked,
    and casts swapped repeats and votes on a retired case, which the round drops.
    """
    rng = np.random.default_rng(7)
    everyone = list(ROUND_STRENGTHS)
    voted, strays = everyone[:5], everyone[5:7]
    cases: dict[str, Category | None] = {
        f'case-{i:02}': ROUND_CATEGORIES[i % 3] for i in range(30)
    }

    outputs: list[OutputMeasurement] = []
    lengths: defaultdict[tuple[str, str], list[int]] = defaultdict(list)

    def produce(case: str, system: str, failing: float) -> None:
        length = int(rng.integers(40, 400))
        failed = bool(rng.random() < failing)
        outputs.append(
            OutputMeasurement(
                case=case,
                system=system,
                errored=False,
                verdict='fail' if failed else 'pass',
                reward=None,
                cost_usd=length * 2e-5 * (1 + everyone.index(system)),
                latency_sec=float(rng.uniform(2, 40)),
                output_tokens=length * 2,
            )
        )
        if not failed:
            lengths[case, system].append(length)

    for case in cases:
        for system in voted:
            for _ in range(2):
                produce(case, system, 0.03)
    failing = {output.case for output in outputs if output.verdict == 'fail'}
    clean = [case for case in cases if case not in failing][:2]
    for case in clean:
        for system in strays:
            for _ in range(2):
                produce(case, system, 0.0)
    for case in cases:
        for _ in range(2):
            produce(case, 'acme-tiny', 0.0 if case in clean else 0.7)

    ordinary: list[HumanEvidence] = []

    def vote(session: str, actor: str, case: str, left: str, right: str) -> None:
        length_a, length_b = (
            lengths[case, side][int(rng.integers(len(lengths[case, side])))]
            for side in (left, right)
        )
        gap = ROUND_STRENGTHS[left] - ROUND_STRENGTHS[right] + 0.1
        draw = rng.random()
        choice: Choice = (
            'tie'
            if draw < 0.15
            else 'both_bad'
            if draw < 0.2
            else 'a'
            if rng.random() < 1 / (1 + np.exp(-gap))
            else 'b'
        )
        tagged: tuple[Defect, ...] = (
            (PROSE_DEFECTS[int(rng.integers(len(PROSE_DEFECTS)))],)
            if rng.random() < 0.2
            else ()
        )
        ordinary.append(
            HumanEvidence(
                receipt=f'receipt-{len(ordinary)}',
                actor=actor,
                session=session,
                choice=choice,
                defects_a=tagged if choice != 'a' else (),
                defects_b=tagged if choice == 'a' else (),
                length_a=length_a,
                length_b=length_b,
                comparison=Comparison(
                    left, right, ranking.LEFT_SCORE[choice], case, 1.0
                ),
            )
        )

    dropped = 0
    for day in range(sessions):
        session = f'session-{day:02}'
        actor = 'owner' if day < OWNER_SESSIONS else f'visitor-{day:02}'
        for case in rng.permutation(list(cases)):
            if day > 0 and rng.random() < 0.1:
                dropped += 1
                continue
            left, right = (str(system) for system in rng.choice(voted, 2, False))
            if lengths[case, left] and lengths[case, right]:
                vote(session, actor, str(case), left, right)
        for case in clean:
            vote(session, actor, case, *strays)
            vote(session, actor, case, *reversed(strays))
        dropped += 2

    weights = actor_weights([row.actor for row in ordinary])
    weighted = tuple(
        replace(row, comparison=row.comparison._replace(weight=weight))
        for row, weight in zip(ordinary, weights, strict=True)
    )
    accepted = len(weighted) + dropped
    return AnalysisInput(
        family='arena',
        reference='acme-large',
        systems=tuple(everyone),
        defects=PROSE_DEFECTS,
        provenance=LedgerProvenance(
            status='ledger',
            schema_version=1,
            export_id=f'export-{sessions:02}',
            export_digest='a' * 64,
            namespace='synthetic',
            captured_at=captured_at,
            event_cutoff=accepted,
            receipt_cutoff=accepted,
            family_revision=sessions,
            analysis_sequence=accepted,
            evaluation_id='evaluation-1',
            publication_id='publication-1',
            policy_id='b' * 64,
            sampling_point_snapshot_id='points-before',
            weighted_evidence_digest='c' * 64,
            fitted_point_snapshot_id='points-after',
        ),
        declared_categories=ROUND_CATEGORIES,
        categories=tuple(cases.items()),
        ordinary=weighted,
        settlements=_settlements(outputs),
        outputs=tuple(outputs),
        sessions=tuple(dict.fromkeys(row.session for row in weighted)),
        accepted=accepted,
    )


def _settlements(outputs: list[OutputMeasurement]) -> tuple[Comparison, ...]:
    """Settle every pair with a gate failure: fail loses, and two fails tie."""
    verdicts: defaultdict[str, defaultdict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for output in outputs:
        verdicts[output.case][output.system].append(output.verdict)

    rows: list[Comparison] = []
    for case, systems in verdicts.items():
        for left, right in combinations(sorted(systems), 2):
            weight = 1 / (len(systems[left]) * len(systems[right]))
            for a, b in product(systems[left], systems[right]):
                if 'fail' in (a, b):
                    score = 0.5 if a == b else float(b == 'fail')
                    rows.append(
                        Comparison(left, right, score, case, weight, sided=False)
                    )

    return tuple(rows)
