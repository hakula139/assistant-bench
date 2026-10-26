"""Explicitly approved numerical and rendering projections for an arena release."""

from collections import defaultdict
from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from assistant_bench.families import Case, Family, VotesSettings
from assistant_bench.outputs import Output as CollectedOutput
from assistant_bench.report import INFERENCE, METHOD, RULES
from assistant_bench.scoring.length import text_length
from assistant_bench_arena.ledger_models import (
    Case as FrozenCase,
    Evaluation,
    Output,
    Point,
    Publication,
    RenderedOutput,
    System,
)
from assistant_bench_arena.models import FamilyView
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
    CaseContentApproval,
    CaseMetadata,
    EvaluationManifest,
    FamilyDefinition,
    OutputContentApproval,
    OutputMetadata,
    Presentation,
    PresentedCase,
    PublicApproval,
)


POLICY = AnalysisPolicy.model_validate(
    {
        'method': METHOD.model_dump(),
        'rules': RULES.model_dump(),
        'inference': INFERENCE.model_dump(),
    }
)


def public_catalog(releases: Sequence[Release]) -> tuple[FamilyView, ...]:
    """Expose approved family labels without numerical or rendering inventories."""
    catalog: dict[str, FamilyView] = {}

    for release in releases:
        presentation = release.publication.presentation
        family = FamilyView(
            name=presentation.family,
            aspect=presentation.aspect,
            display_name=presentation.display_name,
            defects=list(release.evaluation.manifest.family.defects),
        )
        existing = catalog.get(family.name)

        if existing is not None and existing != family:
            raise ValueError('A public family has conflicting display metadata.')

        catalog[family.name] = family

    return tuple(catalog[name] for name in sorted(catalog))


def case_key(case: Case, source_digest: str) -> str:
    """Bind the instruction and checklist to verified agent-input source bytes."""
    checklist: list[JsonValue] = list(case.checklist)
    return digest(
        {
            'source_digest': source_digest,
            'instruction': case.instruction,
            'checklist': checklist,
        }
    )


def output_key(output: CollectedOutput, case: str) -> str:
    if output.fingerprint is None:
        raise ValueError('Published output identity requires a known fingerprint.')

    return digest(
        {
            'source_digest': source_identity(
                case,
                output.system,
                output.fingerprint,
                output.job,
                output.trial,
            ),
            'content_digest': digest(output.text),
        }
    )


def publication_settings(family: Family) -> VotesSettings:
    """Require the rendered-case source supported by public preparation."""
    if family.source != 'cases':
        raise ValueError('Public preparation supports rendered case families only.')

    if not isinstance(family.settings, VotesSettings):
        raise ValueError('Only a votes family can prepare an arena release.')

    return family.settings


def prepare(
    family: Family,
    cases: Sequence[Case],
    outputs: Sequence[CollectedOutput],
    *,
    source_digests: Mapping[str, str],
    system_names: Mapping[str, str],
    system_fingerprints: Mapping[str, str],
    approval: PublicApproval,
) -> Release:
    """Prepare the exact explicitly approved public subset.

    `source_digests` must come from verified rendered-task inputs. Case names
    alone cannot establish them.
    """
    settings = publication_settings(family)

    if not settings.public_voting or approval.family != family.name:
        raise ValueError('Public voting requires this family to opt in and approve it.')

    approved_cases = {item.case_key: item for item in approval.cases}
    approved_outputs = {item.output_key: item for item in approval.outputs}
    cases = sorted(cases, key=lambda case: case.id)
    keys = {case.id: case_key(case, source_digests[case.id]) for case in cases}
    collected: dict[str, CollectedOutput] = {}

    for output in outputs:
        key = output_key(output, keys[output.case])
        previous = collected.get(key)
        if previous is not None and previous != output:
            raise ValueError('One output identity has conflicting collected facts.')

        collected[key] = output

    if set(approved_cases) - set(keys.values()) or set(approved_outputs) - set(
        collected
    ):
        raise ValueError('The approval names content absent from these exact inputs.')

    selected_cases: list[FrozenCase] = []
    presented: dict[str, PresentedCase] = {}

    for case in cases:
        key = keys[case.id]
        if key not in approved_cases:
            continue

        document = PresentedCase(
            instruction=case.instruction, checklist=tuple(case.checklist)
        )
        case_consent = approved_cases[key]
        if (
            case.source not in ('public', 'synthetic')
            or case_consent.source != case.source
            or case_consent.instruction_digest != digest(case.instruction)
            or case_consent.presentation_digest != digest(document)
        ):
            raise ValueError('The case approval does not match its source and content.')

        metadata = CaseMetadata(
            source_digest=source_digests[case.id],
            approval=(
                CaseContentApproval.model_validate(
                    case_consent.model_dump(exclude={'presentation_digest'})
                )
            ),
        )
        selected_cases.append(
            FrozenCase(
                key=key,
                category=case.category,
                source=(
                    case.source if case.source in ('public', 'synthetic') else 'private'
                ),
                instruction_digest=digest(case.instruction),
                metadata=metadata,
            )
        )
        presented[key] = document

    selected = {case.key for case in selected_cases}
    frozen_outputs: list[Output] = []
    selected_sources: list[CollectedOutput] = []
    rendered: list[RenderedOutput] = []

    for key, output in sorted(collected.items()):
        frozen_case = keys[output.case]
        if frozen_case not in selected or key not in approved_outputs:
            continue

        if output.fingerprint is None:
            raise ValueError('Published output identity requires a known fingerprint.')

        selected_sources.append(output)

        content_digest = digest(output.text)
        output_consent = approved_outputs[key]
        if (
            output_consent.content_digest != content_digest
            or output_consent.render_digest
            != digest({'render': settings.render, 'content': output.text})
        ):
            raise ValueError(
                'The output approval does not match its content and render.'
            )

        frozen_outputs.append(
            Output(
                key=key,
                case_key=frozen_case,
                system_id=output.system,
                content_digest=content_digest,
                verdict=(
                    'ungraded'
                    if output.gate_failures is None
                    else 'fail'
                    if output.gate_failures
                    else 'pass'
                ),
                length=text_length(output.text or ''),
                approved_public=True,
                metadata=OutputMetadata(
                    source_digest=source_identity(
                        frozen_case,
                        output.system,
                        output.fingerprint,
                        output.job,
                        output.trial,
                    ),
                    errored=output.error is not None,
                    reward=output.reward,
                    cost_usd=output.cost_usd,
                    latency_sec=output.latency_sec,
                    output_tokens=output.output_tokens,
                    approval=(
                        OutputContentApproval(
                            output_key=key, content_digest=content_digest
                        )
                    ),
                ),
            )
        )

        if output.text is not None:
            rendered.append(
                RenderedOutput(
                    output_key=key,
                    render=settings.render,
                    content=output.text,
                    content_digest=content_digest,
                    approved_public=True,
                )
            )

    systems = _systems(settings.systems, selected_sources, system_fingerprints)
    definition = FamilyDefinition(
        name=family.name,
        reference=settings.reference,
        systems=tuple(system.id for system in systems),
        categories=tuple(settings.categories),
        defects=tuple(settings.defects),
    )
    manifest = EvaluationManifest(family=definition, policy=POLICY)
    numerics = settlements(frozen_outputs)
    evaluation_id = evaluation_identity(
        manifest, selected_cases, systems, frozen_outputs, numerics
    )
    presentation = Presentation(
        family=family.name,
        aspect=settings.aspect,
        display_name=settings.display_name,
        render=settings.render,
        systems={system.id: system_names[system.id] for system in systems},
        cases=presented,
    )
    publication_id = publication_identity(
        evaluation_id, presentation, approval, rendered
    )
    policy_id = digest(POLICY)
    points = {system.id: Point(score=None, component=None) for system in systems}
    return Release(
        policy=POLICY,
        evaluation=Evaluation(
            id=evaluation_id,
            family=family.name,
            policy_id=policy_id,
            manifest=manifest,
            cases=len(selected_cases),
            systems=len(systems),
            outputs=len(frozen_outputs),
            numerics=len(numerics),
        ),
        cases=tuple(selected_cases),
        systems=tuple(systems),
        outputs=tuple(frozen_outputs),
        numerics=tuple(numerics),
        publication=Publication(
            id=publication_id,
            evaluation_id=evaluation_id,
            presentation=presentation,
            approval=approval,
            outputs=len(rendered),
        ),
        rendered=tuple(rendered),
        points=point_snapshot(evaluation_id, policy_id, points),
    )


def _systems(
    order: Sequence[str],
    outputs: Sequence[CollectedOutput],
    configured: Mapping[str, str],
) -> list[System]:
    fingerprints: defaultdict[str, set[str]] = defaultdict(set)

    for output in outputs:
        if output.fingerprint is None:
            raise ValueError('Published output identity requires a known fingerprint.')

        fingerprints[output.system].add(output.fingerprint)

    for system, values in fingerprints.items():
        if system in configured:
            values.add(configured[system])

    if any(len(values) != 1 for values in fingerprints.values()):
        raise ValueError(
            'Each published system requires one configuration fingerprint.'
        )

    names = [system for system in order if system in fingerprints]
    names.extend(sorted(set(fingerprints) - set(names)))
    return [
        System(id=system, fingerprint=next(iter(fingerprints[system])), order=index)
        for index, system in enumerate(names)
    ]
