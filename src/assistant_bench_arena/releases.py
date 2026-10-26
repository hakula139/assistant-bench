"""Release identities and validation shared by preparation and ledger sealing."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations, product
from typing import Self

from pydantic import JsonValue, model_validator

from assistant_bench_arena.ledger_models import (
    Case,
    Evaluation,
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
from assistant_bench_arena.transport import OwnerInputError


class EvaluationBundle(Record):
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
        validate_case(case)

    for output in bundle.outputs:
        validate_output(output)

        if output.case_key not in cases or output.system_id not in systems:
            raise ValueError('Every output must belong to its frozen case and system.')

        if output.verdict == 'pass' and (
            output.content_digest == digest(None) or output.metadata.errored
        ):
            raise ValueError('An absent or errored output cannot pass its gate.')

        if output.key != digest(
            {
                'source_digest': output.metadata.source_digest,
                'content_digest': output.content_digest,
            }
        ):
            raise ValueError('An output key must bind its exact source and content.')

    if bundle.numerics != settlements(bundle.outputs):
        raise ValueError('Settlements must use the complete frozen output inventory.')


def validate_case(case: Case) -> None:
    approval = case.metadata.approval

    if (
        case.source == 'private'
        or approval is None
        or approval.case_key != case.key
        or approval.source != case.source
        or approval.instruction_digest != case.instruction_digest
    ):
        raise OwnerInputError(
            'Every public case needs exact redistributable content approval.'
        )


def validate_output(output: Output) -> None:
    approval = output.metadata.approval

    if (
        not output.approved_public
        or approval is None
        or approval.output_key != output.key
        or approval.content_digest != output.content_digest
    ):
        raise OwnerInputError('Every public output needs exact content approval.')


def validate_presentation(bundle: EvaluationBundle, publication: Publication) -> None:
    presentation = publication.presentation
    cases = {case.key: case for case in bundle.cases}
    outputs = {output.key: output for output in bundle.outputs}
    approval = publication.approval

    if (
        publication.evaluation_id != bundle.evaluation.id
        or presentation.family != bundle.evaluation.family
        or set(presentation.cases) != set(cases)
        or set(presentation.systems) != {system.id for system in bundle.systems}
        or approval is None
    ):
        raise OwnerInputError(
            'The publication identity and complete sections must agree.'
        )

    case_approvals = {row.case_key: row for row in approval.cases}
    output_approvals = {row.output_key: row for row in approval.outputs}

    if (
        approval.family != bundle.evaluation.family
        or set(case_approvals) != set(cases)
        or set(output_approvals) != set(outputs)
    ):
        raise OwnerInputError(
            'The public approval must cover every exact case and output.'
        )

    for key, case in cases.items():
        document = presentation.cases[key]

        if case.instruction_digest != digest(document.instruction) or key != digest(
            {
                'source_digest': case.metadata.source_digest,
                'instruction': document.instruction,
                'checklist': list(document.checklist),
            }
        ):
            raise OwnerInputError(
                'A presented case must match its frozen source and instruction.'
            )

        consent = case_approvals[key]

        if (
            consent.source != case.source
            or consent.instruction_digest != case.instruction_digest
            or consent.presentation_digest != digest(document)
        ):
            raise OwnerInputError(
                'Every public case presentation needs current approval.'
            )

    if any(
        output_approvals[key].content_digest != output.content_digest
        for key, output in outputs.items()
    ):
        raise OwnerInputError(
            'Every public output needs its exact approved content digest.'
        )


def validate_rendered(publication: Publication, output: RenderedOutput) -> None:
    if (
        output.render != publication.presentation.render
        or digest(output.content) != output.content_digest
        or not output.approved_public
        or publication.approval is None
    ):
        raise OwnerInputError('A rendered output must match its approved presentation.')

    validate_render_approval(publication, output.output_key, output.content)


def validate_render_approval(
    publication: Publication, output_key: str, content: str | None
) -> None:
    approval = publication.approval

    if approval is None:
        raise OwnerInputError('Every public publication needs explicit approval.')

    consent = next(
        (row for row in approval.outputs if row.output_key == output_key), None
    )

    if (
        consent is None
        or consent.content_digest != digest(content)
        or consent.render_digest
        != digest({'render': publication.presentation.render, 'content': content})
    ):
        raise OwnerInputError('Every public rendered output needs current approval.')


def validate_publication(
    bundle: EvaluationBundle,
    publication: Publication,
    rendered: Sequence[RenderedOutput],
) -> None:
    validate_presentation(bundle, publication)

    documents = {row.output_key: row for row in rendered}
    outputs = {output.key: output for output in bundle.outputs}

    if (
        publication.outputs != len(documents)
        or len(documents) != len(rendered)
        or publication.id
        != publication_identity(
            bundle.evaluation.id,
            publication.presentation,
            publication.approval,
            rendered,
        )
    ):
        raise ValueError('The publication identity and complete sections must agree.')

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

        if rendered_output is not None:
            validate_rendered(publication, rendered_output)
        else:
            validate_render_approval(publication, key, None)

    if set(documents) - set(outputs):
        raise ValueError('A publication cannot render an unlisted output.')
