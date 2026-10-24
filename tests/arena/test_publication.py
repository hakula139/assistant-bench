from dataclasses import dataclass, replace

import pytest
from pydantic import ValidationError

from assistant_bench.arena.publication import (
    case_key,
    load_policy,
    output_key,
    prepare,
)
from assistant_bench.families import Case, Family, Gate, VotesSettings
from assistant_bench.outputs import Output
from assistant_bench_arena.releases import Release
from assistant_bench_arena.serialization import digest
from assistant_bench_arena.snapshots import (
    CaseApproval,
    OutputApproval,
    PresentedCase,
    PublicApproval,
    Settlement,
)
from support import make_output


@dataclass(frozen=True)
class Inputs:
    family: Family
    cases: tuple[Case, ...]
    outputs: tuple[Output, ...]
    sources: dict[str, str]
    names: dict[str, str]
    fingerprints: dict[str, str]

    def prepare(self, approval: PublicApproval | None = None) -> Release:
        return prepare(
            self.family,
            self.cases,
            self.outputs,
            source_digests=self.sources,
            system_names=self.names,
            system_fingerprints=self.fingerprints,
            policy=load_policy(),
            approval=self.approve() if approval is None else approval,
        )

    def approve(self) -> PublicApproval:
        settings = self.family.settings
        assert isinstance(settings, VotesSettings)

        case = next(case for case in self.cases if case.id == 'public-case')
        key = case_key(case, self.sources[case.id])
        return PublicApproval(
            family=self.family.name,
            cases=(
                CaseApproval(
                    case_key=key,
                    source='synthetic',
                    attestation='Synthetic content owned by the benchmark.',
                    instruction_digest=digest(case.instruction),
                    presentation_digest=digest(
                        PresentedCase(
                            instruction=case.instruction,
                            checklist=tuple(case.checklist),
                        )
                    ),
                ),
            ),
            outputs=tuple(
                OutputApproval(
                    output_key=output_key(output, key),
                    content_digest=digest(output.text),
                    render_digest=digest(
                        {'render': settings.render, 'content': output.text}
                    ),
                )
                for output in sorted(
                    {
                        output_key(row, key): row
                        for row in self.outputs
                        if row.case == case.id and row.job == 'private-job-0'
                    }.values(),
                    key=lambda row: row.system,
                )
            ),
        )


@pytest.fixture
def inputs() -> Inputs:
    family = Family(
        name='f',
        source='cases',
        settings=VotesSettings(
            scorer='votes',
            display_name='Family',
            systems=['a', 'b'],
            samples=2,
            reference='a',
            categories=['reply'],
            defects=['facts'],
            public_voting=True,
        ),
    )
    cases = (
        Case(
            id='public-case',
            category='reply',
            source='synthetic',
            instruction='Write an opening sentence.',
            checklist=['Give one sentence'],
        ),
        Case(
            id='private-case',
            category='reply',
            source='held-out',
            instruction='private-case-marker',
            checklist=['private-checklist-marker'],
        ),
    )
    outputs = tuple(
        make_output(
            case=case,
            system=system,
            fingerprint=f'fp-{system}',
            job=f'private-job-{sample}',
            trial=f'private-trial-{case}-{system}-{sample}',
            text=text,
            gate_failures=[] if system == 'a' else [Gate.TESTS],
            response_model='private-route-marker',
            request_id='private-request-marker',
            error='private-error-marker' if system == 'b' else None,
            cost_usd=0.01,
            latency_sec=2.0,
            output_tokens=20,
        )
        for case, sample, system, text in (
            ('public-case', 0, 'a', 'Approved output A.'),
            ('public-case', 0, 'b', 'Approved output B.'),
            ('public-case', 1, 'a', 'unapproved-output-marker'),
            ('public-case', 1, 'b', 'unapproved-failed-marker'),
            ('private-case', 0, 'a', 'private-output-marker'),
            ('private-case', 0, 'b', 'private-failed-marker'),
        )
    )
    return Inputs(
        family,
        cases,
        outputs,
        {case.id: digest({'source': case.id}) for case in cases},
        {'a': 'Public A', 'b': 'Public B'},
        {'a': 'fp-a', 'b': 'fp-b'},
    )


class TestPrepare:
    def test_retains_identical_duplicate_solution_facts_only_once(
        self, inputs: Inputs
    ) -> None:
        original = inputs.prepare()
        duplicated = replace(inputs, outputs=(*inputs.outputs, inputs.outputs[0]))

        release = duplicated.prepare()

        assert release == original

    @pytest.mark.parametrize(
        'change',
        [{'gate_failures': [Gate.TESTS]}, {'cost_usd': 1.0}],
        ids=['verdict', 'measurement'],
    )
    def test_rejects_conflicting_facts_for_one_solution_identity(
        self, inputs: Inputs, change: dict[str, list[Gate] | float]
    ) -> None:
        duplicated = replace(
            inputs,
            outputs=(*inputs.outputs, inputs.outputs[0].model_copy(update=change)),
        )

        with pytest.raises(ValueError):
            duplicated.prepare()

    def test_preserves_release_identity_when_input_order_changes(
        self, inputs: Inputs
    ) -> None:
        original = inputs.prepare()
        reordered = replace(
            inputs,
            cases=tuple(reversed(inputs.cases)),
            outputs=tuple(reversed(inputs.outputs)),
        )

        release = reordered.prepare()

        assert release == original

    @pytest.mark.parametrize('fingerprint', [None, 'different-configuration'])
    def test_requires_one_known_configuration_per_system(
        self, inputs: Inputs, fingerprint: str | None
    ) -> None:
        changed = replace(
            inputs,
            outputs=(
                inputs.outputs[0].model_copy(update={'fingerprint': fingerprint}),
                *inputs.outputs[1:],
            ),
        )

        with pytest.raises(ValueError, match='fingerprint'):
            changed.prepare()

    def test_publishes_only_exactly_approved_cases_outputs_and_safe_metadata(
        self, inputs: Inputs
    ) -> None:
        approval = inputs.approve()

        release = inputs.prepare(approval)

        encoded = release.model_dump_json()
        assert 'Approved output A.' in encoded
        assert 'Approved output B.' in encoded
        for marker in (
            'private-case-marker',
            'private-checklist-marker',
            'private-output-marker',
            'private-failed-marker',
            'private-job-',
            'private-trial-',
            'private-route-marker',
            'private-request-marker',
            'private-error-marker',
            'unapproved-output-marker',
            'unapproved-failed-marker',
        ):
            assert marker not in encoded

        assert release.publication.approval == approval
        assert {row.key for row in release.outputs} == {
            row.output_key for row in approval.outputs
        }
        assert {row.key for row in release.cases} == {
            row.case_key for row in approval.cases
        }
        failed = next(row for row in release.outputs if row.system_id == 'b')
        assert failed.metadata.errored is True
        assert failed.verdict == 'fail'

    def test_recomputes_settlement_denominators_from_the_public_subset(
        self, inputs: Inputs
    ) -> None:
        approval = inputs.approve()
        key = approval.cases[0].case_key
        full = approval.model_copy(
            update={
                'outputs': tuple(
                    OutputApproval(
                        output_key=output_key(output, key),
                        content_digest=digest(output.text),
                        render_digest=digest(
                            {'render': 'text', 'content': output.text}
                        ),
                    )
                    for output in inputs.outputs
                    if output.case == 'public-case'
                )
            }
        )
        complete = inputs.prepare(full)

        public = inputs.prepare(approval)

        complete_rows = [
            Settlement.model_validate(row.value) for row in complete.numerics
        ]
        public_rows = [Settlement.model_validate(row.value) for row in public.numerics]
        assert sum(row.weight for row in complete_rows) == pytest.approx(1.0)
        assert sorted(row.weight for row in complete_rows) == [0.25] * 4
        assert [row.weight for row in public_rows] == [1.0]
        assert [row.score for row in public_rows] == [1.0]
        assert public.evaluation.id != complete.evaluation.id

    @pytest.mark.parametrize(
        'change',
        ['instruction', 'checklist', 'source', 'output', 'render', 'missing-output'],
    )
    def test_rejects_content_that_changed_since_approval(
        self, inputs: Inputs, change: str
    ) -> None:
        approval = inputs.approve()
        match change:
            case 'instruction':
                updated = replace(
                    inputs,
                    cases=(
                        inputs.cases[0].model_copy(update={'instruction': 'Changed'}),
                        inputs.cases[1],
                    ),
                )
            case 'checklist':
                updated = replace(
                    inputs,
                    cases=(
                        inputs.cases[0].model_copy(update={'checklist': ['Changed']}),
                        inputs.cases[1],
                    ),
                )
            case 'source':
                updated = replace(
                    inputs,
                    sources={'public-case': 'changed', 'private-case': 'unchanged'},
                )
            case 'output':
                updated = replace(
                    inputs,
                    outputs=(
                        inputs.outputs[0].model_copy(update={'text': 'Changed'}),
                        *inputs.outputs[1:],
                    ),
                )
            case 'render':
                updated = replace(
                    inputs,
                    family=inputs.family.model_copy(
                        update={
                            'settings': inputs.family.settings.model_copy(
                                update={'render': 'html'}
                            )
                        }
                    ),
                )
            case 'missing-output':
                updated = replace(inputs, outputs=inputs.outputs[1:])
            case _:
                raise AssertionError(change)

        with pytest.raises(ValueError):
            updated.prepare(approval)

    def test_keeps_display_changes_out_of_numerical_identity(
        self, inputs: Inputs
    ) -> None:
        approval = inputs.approve()
        original = inputs.prepare(approval)
        changed = replace(
            inputs,
            family=inputs.family.model_copy(
                update={
                    'settings': inputs.family.settings.model_copy(
                        update={'display_name': 'Renamed family'}
                    )
                }
            ),
            names={'a': 'Renamed A', 'b': 'Renamed B'},
        )

        refreshed = changed.prepare(approval)

        assert refreshed.evaluation == original.evaluation
        assert refreshed.points == original.points
        assert refreshed.publication.id != original.publication.id
        assert refreshed.publication.presentation.systems == changed.names

    def test_requires_voting_opt_in_even_when_the_site_lists_the_family(
        self, inputs: Inputs
    ) -> None:
        family = inputs.family.model_copy(
            update={
                'settings': inputs.family.settings.model_copy(
                    update={'public': True, 'public_voting': False}
                )
            }
        )

        with pytest.raises(ValueError, match='opt in'):
            replace(inputs, family=family).prepare(inputs.approve())

    def test_cannot_approve_a_held_out_case_as_synthetic(self, inputs: Inputs) -> None:
        approval = inputs.approve()
        changed = replace(
            inputs,
            cases=(
                inputs.cases[0].model_copy(update={'source': 'held-out'}),
                inputs.cases[1],
            ),
        )

        with pytest.raises(ValueError, match='source and content'):
            changed.prepare(approval)

    @pytest.mark.parametrize(
        ('field', 'value'),
        [
            ('cost_usd', float('nan')),
            ('latency_sec', float('inf')),
            ('output_tokens', -1),
        ],
        ids=['nonfinite-cost', 'nonfinite-latency', 'negative-tokens'],
    )
    def test_rejects_invalid_numerical_metadata(
        self, inputs: Inputs, field: str, value: float
    ) -> None:
        changed = replace(
            inputs,
            outputs=(
                inputs.outputs[0].model_copy(update={field: value}),
                *inputs.outputs[1:],
            ),
        )

        with pytest.raises(ValidationError):
            changed.prepare(inputs.approve())

    @pytest.mark.parametrize(
        ('field', 'value'),
        [('text', None), ('error', 'TimeoutError')],
        ids=['absent-output', 'errored-output'],
    )
    def test_rejects_passing_output_facts_that_cannot_be_presented(
        self, inputs: Inputs, field: str, value: str | None
    ) -> None:
        changed = replace(
            inputs,
            outputs=(
                inputs.outputs[0].model_copy(update={field: value}),
                *inputs.outputs[1:],
            ),
        )

        with pytest.raises(ValueError, match='absent or errored output cannot pass'):
            changed.prepare()

    def test_preserves_solution_identity_across_regrading(self, inputs: Inputs) -> None:
        original = inputs.prepare()
        changed = replace(
            inputs,
            outputs=(
                inputs.outputs[0].model_copy(update={'gate_failures': [Gate.TESTS]}),
                *inputs.outputs[1:],
            ),
        )

        regraded = changed.prepare()

        assert {row.key for row in regraded.outputs} == {
            row.key for row in original.outputs
        }
        assert regraded.evaluation.id != original.evaluation.id
