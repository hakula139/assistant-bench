"""The `bench` command."""

import os
import subprocess
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent
from typing import Annotated, NoReturn

import typer
from dotenv import find_dotenv, load_dotenv
from harbor.models.job.config import JobConfig

from assistant_bench import jobs, manifest, report
from assistant_bench.arena.commands import app as arena_app
from assistant_bench.arena.exports import read_export
from assistant_bench.arena.snapshots import analyze_export
from assistant_bench.catalog import (
    SYSTEMS_FILE,
    System,
    load_export,
    load_systems,
)
from assistant_bench.families import (
    Case,
    Family,
    VotesSettings,
    case_task,
    family_names,
    load_cases,
    load_family,
)
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.endpoint import SDK_HEADERS_VAR
from assistant_bench.messages import code
from assistant_bench.outputs import Output


SYSTEMS_DOC = 'docs/guide/authoring.md#systems'
HARBOR_DOC = 'docs/research/harbor.md#quirks-of-the-pinned-release'
SYSTEM_RENAME_GUIDE = (
    'Give a changed system a new name and keep the old name only in closed '
    f'reports, as `{SYSTEMS_DOC}` describes.'
)

app = typer.Typer(no_args_is_help=True)
app.add_typer(arena_app, name='arena', help='Prepare and manage frozen arena evidence.')


# ── Arguments ──


def _known_families(names: list[str] | None) -> list[str] | None:
    """Return the named families once each, refusing a name with no family."""
    if names is None:
        return None
    known = family_names()
    unknown = [name for name in dict.fromkeys(names) if name not in known]
    if unknown:
        raise typer.BadParameter(
            f'No family named {code(unknown)}. Choose among {code(known)}.'
        )
    return list(dict.fromkeys(names))


FAMILY_HELP = 'A directory name under families/, such as writing-zh.'
FamilyNames = Annotated[
    list[str] | None,
    typer.Argument(metavar='FAMILY...', help=FAMILY_HELP, callback=_known_families),
]


# ── Commands ──


@app.callback()
def main() -> None:
    """Run families on the systems under test, vote on their outputs, and report."""
    load_dotenv(find_dotenv(usecwd=True))
    os.environ.pop(SDK_HEADERS_VAR, None)
    os.environ.update(jobs.HARBOR_ENV)


@app.command()
def check(
    families: FamilyNames = None,
    task_names: Annotated[
        list[str] | None,
        typer.Option(
            '--task',
            help='Check only this task in one named task family. Repeat for several.',
        ),
    ] = None,
) -> None:
    """Validate every family, or the named ones.

    For a task family, also run each task's reference solution and a do-nothing
    agent in Docker, several times each, so the solution must pass the task's
    tests and the unsolved task must fail them.
    """
    if task_names and (not families or len(families) != 1):
        raise typer.BadParameter('Select exactly one task family with `--task`.')

    task_families: list[tuple[Family, list[Path]]] = []
    for family in map(_load_family, families or family_names()):
        _family_systems(family)
        if family.source == 'cases':
            if task_names:
                raise typer.BadParameter(
                    f'Family `{family.name}` has cases. Select a task family with '
                    '`--task`.'
                )
            cases = len(_render_tasks(family))
            typer.echo(f'Family `{family.name}`: {cases} valid cases.')
            continue
        tasks = _task_dirs(family)
        if task_names:
            known = [task.name for task in tasks]
            unknown = [name for name in dict.fromkeys(task_names) if name not in known]
            if unknown:
                raise typer.BadParameter(
                    f'Family `{family.name}` has no task named {code(unknown)}. '
                    f'Choose among {code(known)}.'
                )
            tasks = [task for task in tasks if task.name in task_names]
        task_families.append((family, tasks))

    failed = False
    for family, tasks in task_families:
        outputs = _read_trials(family, _run(jobs.check_job(family, tasks)))
        problems = _check_problems(tasks, outputs)
        for problem in problems:
            typer.echo(f'Family `{family.name}`: {problem}.', err=True)
        if not problems:
            checked = 'selected task' if task_names else 'task'
            typer.echo(
                f'Family `{family.name}`: every reference solution passed its tests, '
                f'and the do-nothing agent failed every {checked}.'
            )
        failed = failed or bool(problems)
    if failed:
        raise typer.Exit(1)


@app.command()
def run(
    families: FamilyNames = None,
    names: Annotated[
        list[str] | None,
        typer.Option(
            '--system',
            help='Limit the run to this system. Without a family, this sends it '
            'to every family that lists it. Repeat for several.',
        ),
    ] = None,
) -> None:
    """Generate the missing trials of every family, or the named ones, and collect them.

    A run without a family sends every family that lists systems. Each trial sends
    paid requests to the endpoint configured in .env.
    """
    prepared: list[tuple[Family, list[System], list[Path]]] = []
    for family, systems in _run_targets(families, names):
        if not systems:
            _fail(
                f'Family `{family.name}` lists no systems. Add them to `systems` in '
                f'`families/{family.name}/family.toml`.'
            )
        _refuse_drift(family, systems)
        tasks = (
            _render_tasks(family) if family.source == 'cases' else _task_dirs(family)
        )
        prepared.append((family, systems, tasks))

    for family, systems, tasks in prepared:
        try:
            _run(jobs.job(family, systems, tasks))
        finally:
            _collect(family)


@app.command()
def collect(families: FamilyNames = None) -> None:
    """Collect the trials of every family with jobs, or of the named ones.

    The outputs go to outputs/<family>.jsonl in the data directory.
    """
    names = families if families is not None else _families_with_jobs()
    if not names:
        typer.echo('No family has jobs yet. Run `uv run bench run` first.')
        return

    for name in names:
        _collect(_load_family(name))


@app.command()
def close(
    family: Annotated[str, typer.Argument(metavar='FAMILY', help=FAMILY_HELP)],
    snapshot: Annotated[
        Path | None,
        typer.Option(
            help='Close a saved ledger export without collecting current trials.'
        ),
    ] = None,
) -> None:
    """Write a frozen vote report or aggregate a test-scored family's trials."""
    if snapshot is not None:
        try:
            frozen = analyze_export(read_export(snapshot))
            if frozen.analysis.family != family:
                raise ValueError('The snapshot belongs to another family.')

            closed_at = datetime.now(UTC)
            closed = report.close_snapshot(
                frozen.analysis,
                closed_at,
                fingerprints=frozen.fingerprints,
                system_names=frozen.system_names,
                previous_reports=report.read_reports(family, closed_at),
            )
        except (OSError, ValueError) as error:
            _fail(str(error))

        typer.echo(f'Report written to `{report.write_report(closed)}`.')
        return

    _known_families([family])
    selected = _load_family(family)
    if isinstance(selected.settings, VotesSettings):
        _fail('Votes require a saved canonical D1 export. Use `--snapshot <path>`.')

    _collect(selected)
    try:
        closed_at = datetime.now(UTC)
        systems = _load_systems()
        closed = report.close(
            selected,
            closed_at,
            system_names={
                name: system.display_name for name, system in systems.items()
            },
            previous_reports=report.read_reports(family, closed_at),
        )
    except ValueError as error:
        _fail(str(error))

    typer.echo(f'Report written to `{report.write_report(closed)}`.')


@app.command()
def fingerprints() -> None:
    """Fail when a system left the fingerprint its family's closed reports froze."""
    try:
        moved = [
            _moved_line(family, *change)
            for family, members in manifest.runnable_systems(_load_systems()).items()
            for change in _moved(report.reported_fingerprints(family), members)
        ]
    except ValueError as error:
        _fail(str(error))
    if moved:
        _fail('\n'.join([*moved, SYSTEM_RENAME_GUIDE]))
    typer.echo('Every system keeps the fingerprint its closed reports froze.')


# ── Helpers ──


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _load_family(name: str) -> Family:
    try:
        return load_family(name)
    except ValueError as error:
        _fail(str(error))


def _load_systems() -> dict[str, System]:
    try:
        return load_systems(load_export())
    except (RuntimeError, ValueError) as error:
        _fail(str(error))


def _load_cases(family: Family) -> list[Case]:
    try:
        return load_cases(family)
    except ValueError as error:
        _fail(str(error))


def _render_tasks(family: Family) -> list[Path]:
    """Render a case family's tasks, refusing any that would abort its job."""
    tasks = jobs.render_tasks(family, _load_cases(family))
    changed = _changed_tasks(family, tasks)
    if changed:
        _fail(
            f'Family `{family.name}` has cases whose rendered task changed under a '
            f'version a trial recorded: {code([case for case, _ in changed])}. A '
            'Harbor upgrade that changes a `task.toml` default does this, and '
            "Harbor aborts the job until the family's earlier job directories "
            f'are removed, as `{HARBOR_DOC}` describes.'
        )
    return tasks


def _task_dirs(family: Family) -> list[Path]:
    """Return a task family's task directories once each is fit to run."""
    tasks = [case_task(family, case.id) for case in _load_cases(family)]
    try:
        jobs.check_tasks(family, tasks)
        if family.settings.systems:
            export = load_export()
            jobs.author_vendors(family, tasks, load_systems(export), export)
    except ValueError as error:
        _fail(str(error))

    changed = _changed_tasks(family, tasks)
    if changed:
        detail = '\n'.join(
            f'Task `{task}` changed since a trial ran it at version `{version}`.'
            for task, version in changed
        )
        _fail(
            dedent(
                """
                Family `{family}` has tasks to bump:
                {detail}
                Bump each task's `[task] version`: the patch for a change no trial
                depends on, such as its solution, the minor for a change to `tests/`
                alone, which regrades its trials, or the major for anything the agent
                sees, which reruns them.
                """
            )
            .strip()
            .format(family=family.name, detail=detail)
        )
    return tasks


def _changed_tasks(family: Family, tasks: list[Path]) -> list[tuple[str, str]]:
    try:
        return jobs.changed_tasks(family, tasks)
    except ValueError as error:
        _fail(str(error))


def _check_problems(tasks: list[Path], outputs: list[Output]) -> list[str]:
    """Return what keeps a check job's trials from proving the tasks sound."""
    problems: list[str] = []
    for agent, label, reward, verdict in (
        ('oracle', 'the reference solution', 1, 'failed'),
        ('nop', 'the do-nothing agent', 0, 'passed'),
    ):
        attempts = [output for output in outputs if output.system == agent]
        errored = sorted(
            {(output.case, output.error) for output in attempts if output.error}
        )
        problems.extend(
            f'{label} errored on `{case}` (`{error}`)' for case, error in errored
        )
        broken = sorted(
            {
                output.case
                for output in attempts
                if not output.error and output.reward != reward
            }
        )
        if broken:
            problems.append(f'{label} {verdict} the tests of {code(broken)}')
        missing = sorted(
            {task.name for task in tasks} - {output.case for output in attempts}
        )
        if missing:
            problems.append(f'{label} left no trial of {code(missing)}')
    return problems


def _family_systems(family: Family) -> list[System]:
    listed: list[System] = []
    if family.settings.systems:
        try:
            listed = jobs.listed_systems(family, _load_systems())
        except ValueError as error:
            _fail(str(error))

    try:
        jobs.check_systems(family, listed)
    except ValueError as error:
        _fail(str(error))
    return jobs.family_systems(family, listed)


def _named_systems(
    family: Family, systems: list[System], names: list[str]
) -> list[System]:
    listed = [system.name for system in systems]
    unknown = [name for name in dict.fromkeys(names) if name not in listed]
    if unknown:
        raise typer.BadParameter(
            f'Family `{family.name}` does not list {code(unknown)}. '
            f'Choose among {code(listed)}.',
            param_hint='--system',
        )
    return [system for system in systems if system.name in names]


def _families_with_jobs() -> list[str]:
    return [name for name in family_names() if jobs.has_jobs(_load_family(name))]


def _run_targets(
    families: list[str] | None, names: list[str] | None
) -> list[tuple[Family, list[System]]]:
    """Return each family to run with the systems to send it."""
    if families is not None:
        targets: list[tuple[Family, list[System]]] = []
        for name in families:
            family = _load_family(name)
            listed = _family_systems(family)
            targets.append(
                (family, _named_systems(family, listed, names) if names else listed)
            )
        return targets

    every = [
        family
        for family in map(_load_family, family_names())
        if family.settings.systems
    ]

    if names is None:
        return [(family, _family_systems(family)) for family in every]

    wanted = list(dict.fromkeys(names))
    listed_names = {name for family in every for name in family.settings.systems}
    unlisted = [name for name in wanted if name not in listed_names]
    if unlisted:
        raise typer.BadParameter(
            f'No family lists {code(unlisted)} in its `systems`. Check the name '
            f'against `{SYSTEMS_FILE}`.',
            param_hint='--system',
        )

    targets = []
    for family in every:
        if not set(family.settings.systems) & set(wanted):
            continue
        chosen = [system for system in _family_systems(family) if system.name in wanted]
        targets.append((family, chosen))
    return targets


def _refuse_drift(family: Family, systems: list[System]) -> None:
    try:
        drifted = jobs.drifted_systems(family, systems)
        frozen = report.reported_fingerprints(family.name)
    except ValueError as error:
        _fail(str(error))
    moved = _moved(frozen, systems)
    if not drifted and not moved:
        return
    renamed = {name for name, _, _ in moved} | {
        name for name, _, _ in drifted if name in frozen and frozen[name] is None
    }
    rerun = sorted({name for name, _, _ in drifted} - renamed)
    lines = [
        f'Family `{family.name}` has systems that changed their fingerprints:',
        *(_moved_line(family.name, *change) for change in moved),
        *(
            f'System `{name}` ran under fingerprint `{recorded}` but now resolves '
            f'to `{current}`.'
            for name, recorded, current in drifted
        ),
    ]
    if renamed:
        lines.append(
            f'Closed reports name {code(sorted(renamed))}. {SYSTEM_RENAME_GUIDE}'
        )
    if rerun:
        lines.append(
            f'Delete the stale trials of {code(rerun)} under `jobs/{family.name}/` '
            'in the data directory before the next run.'
        )
    _fail('\n'.join(lines))


def _moved(
    frozen: Mapping[str, str | None], systems: list[System]
) -> list[tuple[str, str, str]]:
    """Return each system whose fingerprint differs from a frozen one, with both."""
    moved: list[tuple[str, str, str]] = []
    for system in systems:
        recorded = frozen.get(system.name)
        current = fingerprint(system)
        if recorded is not None and recorded != current:
            moved.append((system.name, recorded, current))
    return moved


def _moved_line(family: str, name: str, recorded: str, current: str) -> str:
    return (
        f'Closed reports of `{family}` froze `{name}` under fingerprint '
        f'`{recorded}`, but it now resolves to `{current}`.'
    )


def _collect(family: Family) -> None:
    systems = _load_systems()
    try:
        outputs = jobs.collect(family, systems)
    except ValueError as invalid:
        _fail(str(invalid))

    errors = Counter(output.error for output in outputs if output.error)
    typer.echo(
        f'Family `{family.name}`: {len(outputs)} outputs, '
        f'{sum(errors.values())} errored.'
    )
    for error, count in errors.most_common():
        typer.echo(f'  {error}: {count}')

    gated = [output for output in outputs if output.gate_failures]
    if gated:
        typer.echo(f'{len(gated)} failed a gate.')
        failures = Counter(
            gate for output in gated for gate in output.gate_failures or []
        )
        for gate, count in failures.most_common():
            typer.echo(f'  {gate}: {count}')

    rewards = [output.reward for output in outputs if output.reward is not None]
    if rewards:
        typer.echo(f'Mean reward {sum(rewards) / len(rewards):.3f}.')

    graded = {output.case for output in outputs if output.reward is not None}
    solved = {output.case for output in outputs if (output.reward or 0) >= 1}
    if unsolved := sorted(graded - solved):
        typer.echo(f'No system solved {code(unsolved)}.')

    if family.source == 'tasks' and family.settings.systems:
        _author_rates(family, outputs, systems)

    versions = sorted(
        {output.harness_version for output in outputs if output.harness_version}
    )
    if len(versions) > 1:
        typer.echo(f'The outputs span Harbor versions {code(versions)}.')


def _author_rates(
    family: Family, outputs: list[Output], systems: dict[str, System]
) -> None:
    tasks = [case_task(family, case.id) for case in _load_cases(family)]
    try:
        vendors = jobs.author_vendors(family, tasks, systems, load_export())
    except ValueError as error:
        _fail(str(error))

    for system in _family_systems(family):
        name = system.name
        rates: list[str] = []
        for own, label in ((True, 'own-vendor'), (False, 'other-vendor')):
            trials = [
                output
                for output in outputs
                if output.system == name
                and (vendors[output.case] == system.vendor) == own
            ]
            passed = sum((output.reward or 0) >= 1 for output in trials)
            rate = (
                f'{passed}/{len(trials)} ({passed / len(trials):.1%})'
                if trials
                else 'no trials'
            )
            rates.append(f'{label} tasks {rate}')

        typer.echo(f'System `{name}`: pass rate on {", ".join(rates)}.')


def _run(job: JobConfig) -> Path:
    """Run the job and print its directory, exiting with Harbor's code on failure."""
    try:
        path = jobs.run_job(job)
    except subprocess.CalledProcessError as error:
        failure = f'Harbor exited with code {error.returncode}.'
        if jobs.job_path(job).is_dir():
            failure += f' Browse its trials with `harbor view {jobs.job_path(job)}`.'
        typer.echo(failure, err=True)
        raise typer.Exit(error.returncode) from error

    typer.echo(f'Job written to `{path}`.')
    return path


def _read_trials(family: Family, job_dir: Path) -> list[Output]:
    try:
        return jobs.read_trials(family, job_dir, {})
    except ValueError as error:
        _fail(str(error))
