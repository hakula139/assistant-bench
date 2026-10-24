"""Bind explicit publication selections to current collection and native task bytes."""

from harbor.models.job.lock import TrialLock
from pydantic import Field

from assistant_bench import jobs
from assistant_bench.arena.publication import load_policy, prepare
from assistant_bench.arena.sources import prove_task
from assistant_bench.catalog import load_export, load_systems
from assistant_bench.families import case_task, load_cases, load_family
from assistant_bench.outputs import Output
from assistant_bench.paths import data_dir
from assistant_bench_arena.models import FamilyName, Record
from assistant_bench_arena.releases import Release
from assistant_bench_arena.snapshots import PublicApproval


class SelectedOutput(Record):
    case: str
    system: str
    job: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')
    trial: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')


class Preparation(Record):
    family: FamilyName
    outputs: tuple[SelectedOutput, ...] = Field(min_length=1)
    approval: PublicApproval


def prepare_selection(selection: Preparation) -> Release:
    """Recollect current inputs and refuse unproved or stale selected solutions."""
    family = load_family(selection.family)
    cases = {case.id: case for case in load_cases(family)}
    known = load_systems(load_export())

    if any(name not in known for name in family.settings.systems):
        raise ValueError('The family lists a system absent from the current catalog.')

    listed = [known[name] for name in family.settings.systems]
    jobs.check_systems(family, listed)
    systems = jobs.family_systems(family, listed)
    collected = jobs.collect(family, known)
    outputs: list[Output] = []
    sources: dict[str, str] = {}
    seen: set[SelectedOutput] = set()

    for selected in selection.outputs:
        if selected in seen:
            raise ValueError('A preparation must select each solution once.')

        seen.add(selected)
        matches = [
            output
            for output in collected
            if (output.case, output.system, output.job, output.trial)
            == (selected.case, selected.system, selected.job, selected.trial)
        ]

        if len(matches) != 1 or selected.case not in cases:
            raise ValueError('A selected solution is absent from current collection.')

        output = matches[0]
        trial = data_dir() / 'jobs' / family.name / selected.job / selected.trial
        lock = TrialLock.model_validate_json((trial / 'lock.json').read_bytes())
        proof = prove_task(case_task(family, selected.case), lock.task.digest)

        if proof.instruction != cases[selected.case].instruction:
            raise ValueError('The selected solution received another instruction.')

        previous = sources.get(selected.case)

        if previous is not None and previous != proof.source_digest:
            raise ValueError('Selected solutions received different case inputs.')

        sources[selected.case] = proof.source_digest
        outputs.append(output)

    return prepare(
        family,
        [case for name, case in cases.items() if name in sources],
        outputs,
        source_digests=sources,
        system_names={system.name: system.display_name for system in systems},
        system_fingerprints={system.name: system.fingerprint for system in systems},
        policy=load_policy(),
        approval=selection.approval,
    )
