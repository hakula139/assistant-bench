"""Synthetic frozen evidence shared by offline report and snapshot tests."""

from dataclasses import replace
from datetime import UTC, datetime

from assistant_bench.arena.snapshots import (
    AnalysisInput,
    HumanEvidence,
    OutputMeasurement,
)
from assistant_bench.scoring import ranking
from assistant_bench.scoring.ranking import Comparison
from assistant_bench.scoring.weighting import actor_weights
from assistant_bench_arena.models import Choice
from assistant_bench_arena.snapshots import (
    AnalysisPolicy,
    CappedHumanWeights,
    FamilyDefinition,
    LedgerProvenance,
    SettledWeights,
    UnavailableInference,
    VotesMethod,
    VotesRules,
)


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
    score = {'a': 1.0, 'b': 0.0, 'tie': 0.5, 'both_bad': 0.5}[choice]

    return HumanEvidence(
        receipt=f'receipt-{index}',
        actor=actor,
        session=session,
        choice=choice,
        defects_a=(),
        defects_b=(),
        length_a=length_a,
        length_b=length_b,
        comparison=Comparison(*systems, score, f'case-{index}', weight),
    )


def make_analysis(
    ordinary: tuple[HumanEvidence, ...] = (),
    *,
    settlements: tuple[Comparison, ...] = (),
    repeats: tuple[HumanEvidence, ...] = (),
    categories: tuple[tuple[str, str | None], ...] | None = None,
) -> AnalysisInput:
    comparisons = tuple(row.comparison for row in ordinary) + settlements
    outputs = tuple(
        OutputMeasurement(
            case=row.case,
            system=system,
            errored=False,
            verdict='pass',
            cost_usd=0.01,
            latency_sec=2.0,
            output_tokens=20,
        )
        for row in comparisons
        for system in (row.left, row.right)
    )
    return AnalysisInput(
        family=FamilyDefinition(
            name='f',
            reference='a',
            systems=('a', 'b', 'x', 'y', 'unrated'),
            categories=('main', 'other', 'remote'),
            defects=('facts',),
        ),
        policy=AnalysisPolicy(
            method=VotesMethod(
                name='bradley-terry',
                version=1,
                l2_penalty=ranking.L2_PENALTY,
                points_per_logit=ranking.ELO_PER_LOGIT,
                anchor_score=ranking.ANCHOR,
                human_weights=CappedHumanWeights(
                    name='actor-proportional-cap',
                    version=1,
                    cap=100,
                    scope='family-evaluation',
                    repeat_handling='excluded',
                ),
                settled_weights=SettledWeights(name='sample-pair-share', version=1),
            ),
            rules=VotesRules(
                preliminary_decisive_votes=40,
                preliminary_decisive_vote_unit='raw-votes',
                rater_agreement_floor=0.7,
            ),
            inference=UnavailableInference(
                status='unavailable',
                target='new-cases-and-voters',
                reason='population-method-not-validated',
            ),
        ),
        provenance=LedgerProvenance(
            status='ledger',
            schema_version=1,
            export_id='export-1',
            export_digest='a' * 64,
            namespace='test-deployment',
            captured_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_cutoff=len(ordinary) + len(repeats) + 10,
            receipt_cutoff=len(ordinary) + len(repeats),
            family_revision=4,
            analysis_sequence=len(ordinary) + len(repeats) + 10,
            evaluation_id='evaluation-1',
            publication_id='publication-1',
            policy_id='b' * 64,
            sampling_point_snapshot_id='points-before',
            weighted_evidence_digest='c' * 64,
            fitted_point_snapshot_id='points-after',
        ),
        categories=(
            tuple((row.case, 'main') for row in comparisons)
            if categories is None
            else categories
        ),
        ordinary=ordinary,
        repeats=repeats,
        settlements=settlements,
        outputs=outputs,
        sessions=tuple(dict.fromkeys(row.session for row in ordinary)),
        accepted=len(ordinary) + len(repeats),
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
    weights = actor_weights(('owner',) * 250 + ('visitor',) * 25, 100)
    weighted = tuple(
        replace(row, comparison=row.comparison._replace(weight=weight))
        for row, weight in zip(ordinary, weights, strict=True)
    )
    categories = tuple(
        (
            row.comparison.case,
            'remote'
            if index in range(200, 250)
            else 'main'
            if index < 10 or index in range(250, 255)
            else 'other',
        )
        for index, row in enumerate(weighted)
    )
    original = weighted[0]
    repeat = replace(
        original,
        receipt='repeat-0',
        choice='b',
        comparison=original.comparison._replace(
            left='b', right='a', score=0.0, weight=0.0
        ),
        repeat_of=original.receipt,
    )

    return make_analysis(weighted, repeats=(repeat,), categories=categories)
