from assistant_bench_arena.ledger_models import (
    Case,
    Evaluation,
    Mode,
    Output,
    Point,
    Publication,
    RenderedOutput,
    System,
)
from assistant_bench_arena.releases import (
    Release,
    evaluation_identity,
    point_snapshot,
    publication_identity,
    settlements,
)
from assistant_bench_arena.serialization import digest, source_identity
from assistant_bench_arena.snapshots import (
    AnalysisPolicy,
    CappedHumanWeights,
    CaseApproval,
    CaseContentApproval,
    CaseMetadata,
    EvaluationManifest,
    FamilyDefinition,
    OutputApproval,
    OutputContentApproval,
    OutputMetadata,
    Presentation,
    PresentedCase,
    PublicApproval,
    SettledWeights,
    UnavailableInference,
    VotesMethod,
    VotesRules,
)


POLICY = AnalysisPolicy(
    method=VotesMethod(
        name='bradley-terry',
        version=1,
        l2_penalty=0.01,
        points_per_logit=100.0,
        anchor_score=1_000.0,
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
        preliminary_decisive_votes=10,
        preliminary_decisive_vote_unit='raw-votes',
        rater_agreement_floor=0.5,
    ),
    inference=UnavailableInference(
        status='unavailable',
        target='new-cases-and-voters',
        reason='population-method-not-validated',
    ),
)


def case_document(index: int) -> PresentedCase:
    return PresentedCase(
        instruction=f'Synthetic instruction for case {index}.',
        checklist=('Use the requested tone.',),
    )


def case_key(index: int) -> str:
    document = case_document(index)
    return digest(
        {
            'source_digest': digest({'instruction': document.instruction}),
            'instruction': document.instruction,
            'checklist': list(document.checklist),
        }
    )


def output_content(index: int, name: str) -> str:
    return f'Output for case {index} from {name}.'


def output_source(index: int, name: str) -> str:
    return source_identity(
        case_key(index),
        name,
        f'fingerprint:{name}',
        'synthetic-private-job',
        f'original-{name}-trial',
    )


def output_key(index: int, name: str) -> str:
    return digest(
        {
            'source_digest': output_source(index, name),
            'content_digest': digest(output_content(index, name)),
        }
    )


def make_release(
    *,
    mode: Mode = 'local',
    family: str = 'writing',
    suffix: str = '1',
    cases: int = 3,
    failing_case: int | None = None,
) -> Release:
    case_rows = tuple(
        Case(
            key=case_key(index),
            source='synthetic',
            instruction_digest=digest(case_document(index).instruction),
            metadata=CaseMetadata(
                source_digest=digest({'instruction': case_document(index).instruction}),
                approval=CaseContentApproval(
                    case_key=case_key(index),
                    source='synthetic',
                    attestation='Synthetic fixture content.',
                    instruction_digest=digest(case_document(index).instruction),
                ),
            ),
        )
        for index in range(cases)
    )
    systems = tuple(
        System(id=name, fingerprint=f'fingerprint:{name}', order=index)
        for index, name in enumerate(('large', 'small'))
    )
    outputs = tuple(
        Output(
            key=output_key(index, name),
            case_key=case_key(index),
            system_id=name,
            content_digest=digest(output_content(index, name)),
            verdict='fail' if index == failing_case and name == 'large' else 'pass',
            length=len(output_content(index, name)),
            approved_public=True,
            metadata=OutputMetadata(
                source_digest=output_source(index, name),
                errored=False,
                reward=1.0,
                cost_usd=None,
                latency_sec=None,
                output_tokens=None,
                approval=OutputContentApproval(
                    output_key=output_key(index, name),
                    content_digest=digest(output_content(index, name)),
                ),
            ),
        )
        for index in range(cases)
        for name in ('large', 'small')
    )
    numerics = settlements(outputs)
    manifest = EvaluationManifest(
        family=FamilyDefinition(
            name=family,
            reference='large',
            systems=('large', 'small'),
            categories=(),
            defects=('tone',),
        ),
        policy=POLICY,
    )
    evaluation = Evaluation(
        id=evaluation_identity(manifest, case_rows, systems, outputs, numerics),
        family=family,
        policy_id=digest(POLICY),
        manifest=manifest,
        cases=len(case_rows),
        systems=len(systems),
        outputs=len(outputs),
        numerics=len(numerics),
    )
    presentation = Presentation(
        family=family,
        aspect=None,
        display_name=f'{family} {suffix}',
        render='text',
        systems={'large': 'Large', 'small': 'Small'},
        cases={case_key(index): case_document(index) for index in range(cases)},
    )
    rendered = tuple(
        RenderedOutput(
            output_key=output_key(index, name),
            render='text',
            content=output_content(index, name),
            content_digest=digest(output_content(index, name)),
            approved_public=True,
        )
        for index in range(cases)
        for name in ('large', 'small')
    )
    approval = (
        PublicApproval(
            family=family,
            cases=tuple(
                CaseApproval(
                    case_key=case_key(index),
                    source='synthetic',
                    attestation='Synthetic fixture content.',
                    instruction_digest=digest(case_document(index).instruction),
                    presentation_digest=digest(case_document(index)),
                )
                for index in range(cases)
            ),
            outputs=tuple(
                OutputApproval(
                    output_key=output_key(index, name),
                    content_digest=digest(output_content(index, name)),
                    render_digest=digest(
                        {'render': 'text', 'content': output_content(index, name)}
                    ),
                )
                for index in range(cases)
                for name in ('large', 'small')
            ),
        )
        if mode == 'public'
        else None
    )
    publication = Publication(
        id=publication_identity(evaluation.id, presentation, approval, rendered),
        evaluation_id=evaluation.id,
        presentation=presentation,
        approval=approval,
        outputs=len(rendered),
    )

    return Release(
        mode=mode,
        policy=POLICY,
        evaluation=evaluation,
        cases=case_rows,
        systems=systems,
        outputs=outputs,
        numerics=numerics,
        publication=publication,
        rendered=rendered,
        points=point_snapshot(
            evaluation.id,
            evaluation.policy_id,
            {system.id: Point(score=None, component=None) for system in systems},
        ),
    )
