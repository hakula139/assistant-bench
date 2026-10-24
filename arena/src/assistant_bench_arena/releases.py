"""Release identities and validation shared by preparation and ledger sealing."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations, product
from typing import Self

from pydantic import JsonValue, model_validator

from assistant_bench_arena.ledger_models import (
    Case,
    Evaluation,
    Mode,
    NumericalInput,
    Output,
    Point,
    PointSnapshot,
    Publication,
    RenderedOutput,
    System,
)
from assistant_bench_arena.models import Record
from assistant_bench_arena.serialization import digest
from assistant_bench_arena.snapshots import (
    AnalysisPolicy,
    EvaluationManifest,
    Presentation,
    PublicApproval,
    Settlement,
)


class EvaluationBundle(Record):
    mode: Mode
    policy: AnalysisPolicy
    evaluation: Evaluation
    cases: tuple[Case, ...]
    systems: tuple[System, ...]
    outputs: tuple[Output, ...]
    numerics: tuple[NumericalInput, ...]

    @model_validator(mode='after')
    def complete(self) -> Self:
        validate_evaluation(self)
        return self


class Release(EvaluationBundle):
    publication: Publication
    rendered: tuple[RenderedOutput, ...]
    points: PointSnapshot

    @model_validator(mode='after')
    def complete_publication(self) -> Self:
        validate_publication(self, self.publication, self.rendered)
        if (
            self.points.evaluation_id != self.evaluation.id
            or self.points.policy_id != self.evaluation.policy_id
            or set(self.points.points) != {system.id for system in self.systems}
            or self.points.source_export_id is not None
            or any(point.score is not None for point in self.points.points.values())
            or self.points.id
            != digest(self.points.model_dump(mode='json', exclude={'id'}))
        ):
            raise ValueError('A release must contain complete initial unrated points.')

        return self


def point_snapshot(
    evaluation_id: str,
    policy_id: str,
    points: Mapping[str, Point],
    *,
    source_export_id: str | None = None,
    source_export_digest: str | None = None,
    weighted_evidence_digest: str | None = None,
) -> PointSnapshot:
    body: dict[str, JsonValue] = {
        'evaluation_id': evaluation_id,
        'policy_id': policy_id,
        'source_export_id': source_export_id,
        'source_export_digest': source_export_digest,
        'weighted_evidence_digest': weighted_evidence_digest,
        'points': {key: value.model_dump(mode='json') for key, value in points.items()},
    }
    return PointSnapshot.model_validate({'id': digest(body), **body})


def evaluation_identity(
    manifest: EvaluationManifest,
    cases: Sequence[Case],
    systems: Sequence[System],
    outputs: Sequence[Output],
    numerics: Sequence[NumericalInput],
) -> str:
    return digest(
        {
            'manifest': manifest.model_dump(mode='json'),
            'cases': [
                case.model_dump(mode='json')
                for case in sorted(cases, key=lambda row: row.key)
            ],
            'systems': [
                system.model_dump(mode='json')
                for system in sorted(systems, key=lambda row: row.order)
            ],
            'outputs': [
                output.model_dump(mode='json')
                for output in sorted(outputs, key=lambda row: row.key)
            ],
            'numerics': [
                row.model_dump(mode='json')
                for row in sorted(numerics, key=lambda row: (row.section, row.key))
            ],
        }
    )


def publication_identity(
    evaluation_id: str,
    presentation: Presentation,
    approval: PublicApproval | None,
    rendered: Sequence[RenderedOutput],
) -> str:
    approved = (
        approval.model_copy(
            update={
                'cases': tuple(sorted(approval.cases, key=lambda item: item.case_key)),
                'outputs': tuple(
                    sorted(approval.outputs, key=lambda item: item.output_key)
                ),
            }
        )
        if approval is not None
        else None
    )
    return digest(
        {
            'evaluation_id': evaluation_id,
            'presentation': presentation.model_dump(mode='json'),
            'approval': approved.model_dump(mode='json') if approved else None,
            'rendered': [
                row.model_dump(mode='json')
                for row in sorted(rendered, key=lambda row: row.output_key)
            ],
        }
    )


def settlements(outputs: Sequence[Output]) -> tuple[NumericalInput, ...]:
    grouped: defaultdict[str, defaultdict[str, list[Output]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for output in sorted(outputs, key=lambda row: row.key):
        if output.verdict != 'ungraded':
            grouped[output.case_key][output.system_id].append(output)

    rows: list[NumericalInput] = []
    for case, systems in sorted(grouped.items()):
        for left, right in combinations(sorted(systems), 2):
            weight = 1 / (len(systems[left]) * len(systems[right]))
            for a, b in product(systems[left], systems[right]):
                if a.verdict == b.verdict == 'fail':
                    score = 0.5
                elif a.verdict == 'fail':
                    score = 0.0
                elif b.verdict == 'fail':
                    score = 1.0
                elif a.content_digest == b.content_digest:
                    score = 0.5
                else:
                    continue

                value = Settlement(
                    case_key=case,
                    a_output=a.key,
                    b_output=b.key,
                    score=score,
                    weight=weight,
                )
                rows.append(
                    NumericalInput(
                        section='settlements',
                        key=digest(value),
                        value=value.model_dump(mode='json'),
                    )
                )

    return tuple(sorted(rows, key=lambda row: (row.section, row.key)))


def validate_evaluation(bundle: EvaluationBundle) -> None:
    evaluation = bundle.evaluation
    cases = {case.key: case for case in bundle.cases}
    systems = {system.id: system for system in bundle.systems}
    outputs = {output.key: output for output in bundle.outputs}
    numerics = {(row.section, row.key): row for row in bundle.numerics}
    if (
        evaluation.cases != len(cases)
        or len(cases) != len(bundle.cases)
        or evaluation.systems != len(systems)
        or len(systems) != len(bundle.systems)
        or evaluation.outputs != len(outputs)
        or len(outputs) != len(bundle.outputs)
        or evaluation.numerics != len(numerics)
        or len(numerics) != len(bundle.numerics)
    ):
        raise ValueError('The evaluation counts and unique sections must agree.')

    manifest = evaluation.manifest
    if (
        manifest.family.name != evaluation.family
        or manifest.policy != bundle.policy
        or evaluation.policy_id != digest(bundle.policy)
        or tuple(
            system.id for system in sorted(bundle.systems, key=lambda row: row.order)
        )
        != manifest.family.systems
        or tuple(sorted(system.order for system in bundle.systems))
        != tuple(range(len(bundle.systems)))
        or evaluation.id
        != evaluation_identity(
            manifest, bundle.cases, bundle.systems, bundle.outputs, bundle.numerics
        )
    ):
        raise ValueError('The evaluation identity and frozen policy must agree.')

    for case in bundle.cases:
        case_approval = case.metadata.approval
        if bundle.mode == 'public' and (
            case.source == 'private'
            or case_approval is None
            or case_approval.case_key != case.key
            or case_approval.source != case.source
            or case_approval.instruction_digest != case.instruction_digest
        ):
            raise ValueError(
                'Every public case needs exact redistributable content approval.'
            )

    for output in bundle.outputs:
        output_approval = output.metadata.approval
        if output.case_key not in cases or output.system_id not in systems:
            raise ValueError('Every output must belong to its frozen case and system.')

        if output.key != digest(
            {
                'source_digest': output.metadata.source_digest,
                'content_digest': output.content_digest,
            }
        ):
            raise ValueError('An output key must bind its exact source and content.')

        if bundle.mode == 'public' and (
            not output.approved_public
            or output_approval is None
            or output_approval.output_key != output.key
            or output_approval.content_digest != output.content_digest
        ):
            raise ValueError('Every public output needs exact content approval.')

    if bundle.numerics != settlements(bundle.outputs):
        raise ValueError('Settlements must use the complete frozen output inventory.')


def validate_publication(
    bundle: EvaluationBundle,
    publication: Publication,
    rendered: Sequence[RenderedOutput],
) -> None:
    presentation = publication.presentation
    documents = {row.output_key: row for row in rendered}
    cases = {case.key: case for case in bundle.cases}
    outputs = {output.key: output for output in bundle.outputs}
    approval = publication.approval
    if (
        publication.evaluation_id != bundle.evaluation.id
        or presentation.family != bundle.evaluation.family
        or set(presentation.cases) != set(cases)
        or set(presentation.systems) != {system.id for system in bundle.systems}
        or publication.outputs != len(documents)
        or len(documents) != len(rendered)
        or publication.id
        != publication_identity(bundle.evaluation.id, presentation, approval, rendered)
        or (bundle.mode == 'public') != (approval is not None)
    ):
        raise ValueError('The publication identity and complete sections must agree.')

    case_approvals = {row.case_key: row for row in approval.cases} if approval else {}
    output_approvals = (
        {row.output_key: row for row in approval.outputs} if approval else {}
    )
    if approval is not None and (
        approval.family != bundle.evaluation.family
        or set(case_approvals) != set(cases)
        or set(output_approvals) != set(outputs)
    ):
        raise ValueError('The public approval must cover every exact case and output.')

    for key, case in cases.items():
        document = presentation.cases[key]
        if case.instruction_digest != digest(document.instruction) or key != digest(
            {
                'source_digest': case.metadata.source_digest,
                'instruction': document.instruction,
                'checklist': list(document.checklist),
            }
        ):
            raise ValueError(
                'A presented case must match its frozen source and instruction.'
            )

        if approval is not None:
            consent = case_approvals[key]
            if (
                consent.source != case.source
                or consent.instruction_digest != case.instruction_digest
                or consent.presentation_digest != digest(document)
            ):
                raise ValueError(
                    'Every public case presentation needs current approval.'
                )

    for key, output in outputs.items():
        rendered_output = documents.get(key)
        if output.content_digest == digest(None):
            if rendered_output is not None:
                raise ValueError('An absent output cannot have a rendered document.')
        elif (
            rendered_output is None
            or digest(rendered_output.content) != output.content_digest
        ):
            raise ValueError('Every output document must match its frozen content.')

        if rendered_output is not None and (
            rendered_output.render != presentation.render
            or rendered_output.content_digest != output.content_digest
            or (bundle.mode == 'public' and not rendered_output.approved_public)
        ):
            raise ValueError('A rendered output must match its approved presentation.')

        if approval is not None:
            output_consent = output_approvals[key]
            if (
                output_consent.content_digest != output.content_digest
                or output_consent.render_digest
                != digest(
                    {
                        'render': presentation.render,
                        'content': rendered_output.content if rendered_output else None,
                    }
                )
            ):
                raise ValueError('Every public rendered output needs current approval.')

    if set(documents) - set(outputs):
        raise ValueError('A publication cannot render an unlisted output.')
