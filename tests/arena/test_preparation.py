import tomllib
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest
import tomli_w
from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TrialLock
from harbor.models.task.config import TaskConfig
from harbor.models.trial.result import TrialResult
from pydantic import ValidationError

from assistant_bench import jobs
from assistant_bench.arena import preparation
from assistant_bench.arena.preparation import (
    Preparation,
    SelectedOutput,
    prepare_selection,
)
from assistant_bench.arena.publication import case_key, output_key
from assistant_bench.arena.sources import prove_task
from assistant_bench.catalog import NixosConfigExport, System, load_systems
from assistant_bench.families import case_task, load_cases, load_family
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench_arena.serialization import digest
from assistant_bench_arena.snapshots import (
    CaseApproval,
    OutputApproval,
    PresentedCase,
    PublicApproval,
)
from support import PROSE_DEFECTS, finish_job, write_task_family, write_votes_family


@dataclass(frozen=True)
class Selection:
    request: Preparation
    task: Path
    task_digest: str


@pytest.fixture
def selection(
    families_dir: Path, export: NixosConfigExport, monkeypatch: pytest.MonkeyPatch
) -> Selection:
    systems_file = Path('systems.toml')
    systems_file.write_text(
        '[acme-large]\nmodel = "acme-large"\n[acme-small]\nmodel = "acme-small"\n'
    )

    def selected_systems(current: NixosConfigExport) -> dict[str, System]:
        return load_systems(current, systems_file)

    monkeypatch.setattr(preparation, 'load_systems', selected_systems)
    write_votes_family(
        families_dir, 'f', ['acme-large', 'acme-small'], {'greeting': 'reply'}
    )
    settings = families_dir / 'f/family.toml'
    settings.write_text(settings.read_text() + 'public_voting = true\n')
    family = load_family('f')
    known = selected_systems(export)
    cases = load_cases(family)
    tasks = jobs.render_tasks(family, cases)
    job = jobs.job(family, list(known.values()), tasks)
    directory = finish_job(job)

    for path in directory.glob('*/result.json'):
        result = TrialResult.model_validate_json(path.read_text())
        result.agent_result = AgentContext(
            n_output_tokens=20, metadata={'finish_reason': 'stop'}
        )
        path.write_text(result.model_dump_json())
        agent = path.parent / 'agent'
        agent.mkdir()
        (agent / OUTPUT_FILE).write_text('A synthetic greeting for the reader.')

    outputs = jobs.collect(family, known)
    lock = TrialLock.model_validate_json(
        (directory / outputs[0].trial / 'lock.json').read_bytes()
    )
    proof = prove_task(tasks[0], lock.task.digest)
    case = cases[0]
    key = case_key(case, proof.source_digest)
    approval = PublicApproval(
        family='f',
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
                render_digest=digest({'render': 'text', 'content': output.text}),
            )
            for output in outputs
        ),
    )
    request = Preparation(
        family=family.name,
        outputs=tuple(
            SelectedOutput(
                case=output.case,
                system=output.system,
                job=output.job,
                trial=output.trial,
            )
            for output in outputs
        ),
        approval=approval,
    )
    return Selection(request, case_task(family, 'greeting'), lock.task.digest)


class TestPrepareSelection:
    def test_expanding_issue_options_reseals_evaluation_without_regenerating_tasks(
        self, selection: Selection, families_dir: Path
    ) -> None:
        settings = families_dir / 'f/family.toml'
        configuration = tomllib.loads(settings.read_text())
        configuration['defects'] = ['facts']
        settings.write_text(tomli_w.dumps(configuration))
        original = prepare_selection(selection.request)
        original_task = {
            path.name: path.read_bytes()
            for path in selection.task.iterdir()
            if path.is_file()
        }
        original_config = TaskConfig.model_validate_toml(
            (selection.task / 'task.toml').read_text()
        )

        configuration['defects'] = list(PROSE_DEFECTS)
        settings.write_text(tomli_w.dumps(configuration))
        family = load_family('f')
        jobs.render_tasks(family, load_cases(family))
        expanded = prepare_selection(selection.request)

        assert original.evaluation.manifest.family.defects == ('facts',)
        assert expanded.evaluation.manifest.family.defects == PROSE_DEFECTS
        assert expanded.evaluation.id != original.evaluation.id
        assert expanded.publication.id != original.publication.id
        assert expanded.cases == original.cases
        assert expanded.outputs == original.outputs
        assert expanded.rendered == original.rendered
        assert expanded.publication.presentation == original.publication.presentation
        assert {
            path.name: path.read_bytes()
            for path in selection.task.iterdir()
            if path.is_file()
        } == original_task
        current_config = TaskConfig.model_validate_toml(
            (selection.task / 'task.toml').read_text()
        )
        assert current_config.task is not None
        assert original_config.task is not None
        assert current_config.task.version == original_config.task.version

    @pytest.mark.parametrize(
        'systems', [(), ('acme-large', 'acme-small')], ids=['tests', 'votes']
    )
    def test_rejects_authored_families_before_reading_cases_or_collecting_trials(
        self,
        selection: Selection,
        families_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        systems: tuple[str, ...],
    ) -> None:
        write_task_family(families_dir, 'echo', name='authored', systems=systems)
        request = selection.request.model_copy(update={'family': 'authored'})
        read_cases = Mock(side_effect=AssertionError('Authored cases were read.'))
        collect = Mock(side_effect=AssertionError('Authored trials were collected.'))
        monkeypatch.setattr(preparation, 'load_cases', read_cases)
        monkeypatch.setattr(jobs, 'collect', collect)

        with pytest.raises(
            ValueError,
            match=r'^Public preparation supports rendered case families only\.$',
        ):
            prepare_selection(request)

        read_cases.assert_not_called()
        collect.assert_not_called()

    @pytest.mark.parametrize(
        'family', ['../outside', '/outside', 'family\n', 'Uppercase']
    )
    def test_rejects_unsafe_family_names_before_selection(
        self, selection: Selection, family: str
    ) -> None:
        payload = selection.request.model_dump(mode='json')
        payload['family'] = family

        with pytest.raises(ValidationError):
            Preparation.model_validate(payload)

    def test_binds_real_current_collection_to_the_native_task_proof(
        self, selection: Selection
    ) -> None:
        release = prepare_selection(selection.request)
        proof = prove_task(selection.task, selection.task_digest)

        assert release.publication.approval == selection.request.approval
        assert {output.system_id for output in release.outputs} == {
            'acme-large',
            'acme-small',
        }
        assert release.cases[0].metadata.source_digest == proof.source_digest
        assert release.cases[0].instruction_digest == digest(proof.instruction)
        assert all(
            output.content_digest == digest('A synthetic greeting for the reader.')
            for output in release.outputs
        )

    @pytest.mark.parametrize(
        'changed', ['instruction', 'workspace', 'verifier', 'verifier-settings']
    )
    def test_refuses_selected_trials_whose_recorded_task_bytes_changed(
        self, selection: Selection, changed: str
    ) -> None:
        if changed == 'instruction':
            (selection.task / 'instruction.md').write_text('Another instruction.')
        elif changed == 'workspace':
            environment = selection.task / 'environment'
            environment.mkdir()
            (environment / 'Dockerfile').write_text('FROM scratch\n')
        elif changed == 'verifier':
            tests = selection.task / 'tests'
            tests.mkdir()
            (tests / 'test.sh').write_text('exit 0\n')
        else:
            path = selection.task / 'task.toml'
            config = TaskConfig.model_validate_toml(path.read_text())
            config.verifier.timeout_sec = 30
            path.write_text(config.model_dump_toml())

        with pytest.raises(ValueError, match='recorded Harbor digest'):
            prepare_selection(selection.request)

    @pytest.mark.parametrize('invalid', ['duplicate', 'missing-trial'])
    def test_refuses_ambiguous_or_absent_current_selections(
        self, selection: Selection, invalid: str
    ) -> None:
        first = selection.request.outputs[0]
        outputs: tuple[SelectedOutput, ...]

        if invalid == 'duplicate':
            outputs = (first, first)
            reason = 'select each solution once'
        else:
            outputs = (first.model_copy(update={'trial': 'missing'}),)
            reason = 'absent from current collection'

        request = Preparation(
            family='f', outputs=outputs, approval=selection.request.approval
        )

        with pytest.raises(ValueError, match=reason):
            prepare_selection(request)


class TestSelectedOutput:
    @pytest.mark.parametrize('field', ['job', 'trial'])
    @pytest.mark.parametrize('value', ['../outside', '/absolute', 'nested/path', ''])
    def test_refuses_paths_outside_one_selected_trial(
        self, field: str, value: str
    ) -> None:
        values = {'case': 'c', 'system': 's', 'job': 'job', 'trial': 'trial'}
        values[field] = value

        with pytest.raises(ValidationError):
            SelectedOutput.model_validate(values)
