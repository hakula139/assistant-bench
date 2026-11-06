"""The `bench` command."""

import os
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent
from typing import Annotated, Any, assert_never

import typer
from dotenv import find_dotenv, load_dotenv
from harbor.models.job.config import JobConfig
from typer.core import TyperGroup

from assistant_bench import collection, jobs, report
from assistant_bench.arena import votes
from assistant_bench.arena.commands import app as arena_app
from assistant_bench.arena.d1 import D1Error, connect
from assistant_bench.arguments import (
    FamilyArgument,
    FamilyArguments,
    SystemOptions,
    known_names,
)
from assistant_bench.catalog import (
    SYSTEMS_FILE,
    NixosConfigExport,
    System,
    load_export,
    load_systems,
)
from assistant_bench.families import (
    Family,
    TaskTestsSettings,
    VotesSettings,
    case_task,
    check_case_entries,
    family_names,
    load_cases,
    load_family,
)
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.endpoint import SDK_HEADERS_VAR
from assistant_bench.messages import InputError, code, count_of
from assistant_bench.outputs import Output, pool_conflicts, read_outputs, tests_pass
from assistant_bench.spend import (
    Basis,
    Projection,
    Spend,
    budget_problem,
    project,
    summarize,
    total_usd,
    update_ledger,
)
from assistant_bench.tasks import (
    author_vendors,
    changed_tasks,
    check_tasks,
    render_tasks,
)


SYSTEMS_DOC = 'docs/guide/authoring.md#systems'
HARBOR_DOC = 'docs/research/harbor.md#quirks-of-the-pinned-release'
SYSTEM_RENAME_GUIDE = (
    'Give a changed system a new name and keep the old name only in closed '
    f'reports, as `{SYSTEMS_DOC}` describes.'
)
USAGE_SOURCES: dict[Basis, str] = {'own': 'its own', 'borrowed': "another system's"}


class _Group(TyperGroup):
    """Print an author-facing error to stderr and exit with code 1.

    Under Python's development mode the error propagates with its traceback.
    """

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except (D1Error, InputError) as error:
            if sys.flags.dev_mode:
                raise
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error


app = typer.Typer(cls=_Group, no_args_is_help=True)
app.add_typer(
    arena_app, name='arena', help='Publish outputs for voting and inspect votes.'
)


# ── Commands ──


@app.callback()
def main() -> None:
    """Run families on the systems under test, vote on their outputs, and report."""
    load_dotenv(find_dotenv(usecwd=True))
    os.environ.pop(SDK_HEADERS_VAR, None)
    os.environ.update(jobs.HARBOR_ENV)


@app.command()
def check(
    families: FamilyArguments = None,
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
        raise InputError('Select exactly one task family with `--task`.')

    check_case_entries()
    export, systems = _catalog()
    task_families: list[tuple[Family, list[Path]]] = []
    for family in map(load_family, families or family_names()):
        _family_systems(family, systems)
        if family.source == 'cases':
            if task_names:
                raise InputError(
                    f'Family `{family.name}` has cases. Select a task family with '
                    '`--task`.'
                )
            cases = count_of(len(_render_tasks(family)), 'valid case')
            typer.echo(f'Family `{family.name}`: {cases}.')
            continue
        tasks = _task_dirs(family, export)
        if task_names:
            known_names(
                task_names,
                [task.name for task in tasks],
                f'Family `{family.name}` has no task named',
                param_hint="'--task'",
            )
            tasks = [task for task in tasks if task.name in task_names]
        task_families.append((family, tasks))

    failed = False
    for family, tasks in task_families:
        outputs = collection.read_trials(family, _run(jobs.check_job(family, tasks)))
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
def run(families: FamilyArguments = None, names: SystemOptions = None) -> None:
    """Generate the missing trials of every family, or the named ones, and collect them.

    A run without a family sends every family that lists systems. Each trial sends
    paid requests to the endpoint configured in .env. Before any trial starts, the
    run prints each family's projected cost, and one family it would take past its
    `budget_usd` refuses the whole run.
    """
    export, systems = _catalog()
    prepared = _prepare(families, names, export, systems)
    problems: list[str] = []
    for family, selected, job in prepared:
        spent, projections = _projection(family, selected, job, systems)
        _echo_projection(family, spent, projections)
        if (problem := budget_problem(family, spent, projections)) is not None:
            problems.append(problem)
    if problems:
        raise InputError('\n'.join(problems))

    for family, _, job in prepared:
        try:
            _run(job)
        finally:
            _echo_conflicts(family, _collect(family, systems, export))


@app.command()
def spend(families: FamilyArguments = None, names: SystemOptions = None) -> None:
    """Print the spend ledger, and the projection of `bench run` with these arguments.

    Nothing runs, and no request reaches the endpoint.
    """
    export, systems = _catalog()
    for family, selected, job in _prepare(families, names, export, systems):
        spent, projections = _projection(family, selected, job, systems)
        for entry in spent:
            typer.echo(_spend_line(entry))
        _echo_projection(family, spent, projections)
        if (problem := budget_problem(family, spent, projections)) is not None:
            typer.echo(problem)


@app.command()
def collect(families: FamilyArguments = None) -> None:
    """Collect the trials of every family with jobs, or of the named ones.

    The outputs go to outputs/<family>.jsonl in the data directory.
    """
    names = families if families is not None else _families_with_jobs()
    if not names:
        typer.echo('No family has jobs yet. Run `uv run bench run` first.')
        return

    export, systems = _catalog()
    for name in names:
        selected = load_family(name)
        _echo_conflicts(selected, _collect(selected, systems, export))


@app.command()
def close(
    family: FamilyArgument,
    snapshot: Annotated[
        Path | None,
        typer.Option(
            help='Close a saved vote snapshot instead of reading the store.',
            exists=True,
            dir_okay=False,
        ),
    ] = None,
) -> None:
    """Close a round of the family and write its report.

    A votes family takes the votes production can no longer undo, saves them to
    votes/<family>/ in the data directory, and closes from that file. A tests
    family aggregates its collected trials.
    """
    selected = load_family(family)
    match selected.settings:
        case VotesSettings():
            closed = _close_votes(
                selected,
                _take_snapshot(selected) if snapshot is None else snapshot,
            )
        case TaskTestsSettings():
            if snapshot is not None:
                raise InputError(
                    f'Family `{family}` is scored by tests, so it has no votes.'
                )
            closed = _close_tests(selected)
        case _:
            assert_never(selected.settings)

    typer.echo(f'Report written to `{report.write_report(closed)}`.')


def _take_snapshot(family: Family) -> Path:
    """Save production's cut of the family's votes and return the snapshot's path."""
    assert isinstance(family.settings, VotesSettings)
    if not family.settings.public_voting:
        raise InputError(
            f'Family `{family.name}` sets no `public_voting`, so no store holds its '
            'votes.'
        )

    taken_at = datetime.now(UTC)
    with connect('production') as database:
        cut = votes.read_cut(database, family.name, taken_at)

    if cut is None:
        raise InputError(
            f'Family `{family.name}` has no active publication in production, so it '
            'has no votes to close. Publish one with `uv run bench arena publish '
            'production <file>`.'
        )

    path = votes.write_snapshot(
        votes.Snapshot(family=family.name, taken_at=taken_at, production=cut)
    )
    typer.echo(f'Votes saved to `{path}`.')
    return path


def _close_votes(family: Family, path: Path) -> report.Report:
    snapshot = votes.read_snapshot(path)
    return report.close_votes(
        votes.voting_input(snapshot, family, read_outputs(family.name)),
        datetime.now(UTC),
        fingerprints=votes.fingerprints(snapshot),
        system_names=snapshot.production.inventory.systems,
    )


def _close_tests(family: Family) -> report.Report:
    export, systems = _catalog()
    _collect(family, systems, export)
    return report.close_tests(
        family,
        datetime.now(UTC),
        system_names={name: system.display_name for name, system in systems.items()},
    )


@app.command()
def fingerprints() -> None:
    """Fail when a system left the fingerprint its family's closed reports froze."""
    _, systems = _catalog()
    moved = [
        _moved_line(family, *change)
        for family, members in jobs.runnable_systems(systems).items()
        for change in _moved(report.reported_fingerprints(family), members)
    ]
    if moved:
        raise InputError('\n'.join([*moved, SYSTEM_RENAME_GUIDE]))
    typer.echo('Every system keeps the fingerprint its closed reports froze.')


# ── Helpers ──


def _catalog() -> tuple[NixosConfigExport, dict[str, System]]:
    export = load_export()
    return export, load_systems(export)


def _render_tasks(family: Family) -> list[Path]:
    """Render a case family's tasks, refusing any that would abort its job."""
    tasks = render_tasks(family, load_cases(family))
    changed = changed_tasks(family, tasks)
    if changed:
        raise InputError(
            f'Family `{family.name}` has cases whose rendered task changed under a '
            f'version a trial recorded: {code([case for case, _ in changed])}. A '
            'Harbor upgrade that changes a `task.toml` default does this, and '
            "Harbor aborts the job until the family's earlier job directories "
            f'are removed, as `{HARBOR_DOC}` describes.'
        )
    return tasks


def _task_dirs(family: Family, export: NixosConfigExport) -> list[Path]:
    """Return a task family's task directories once each is fit to run."""
    tasks = [case_task(family, case.id) for case in load_cases(family)]
    check_tasks(family, tasks)
    if family.settings.systems:
        author_vendors(family, tasks, export)

    changed = changed_tasks(family, tasks)
    if changed:
        detail = '\n'.join(
            f'Task `{task}` changed since a trial ran it at version `{version}`.'
            for task, version in changed
        )
        raise InputError(
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
        completed = [output for output in attempts if not output.error]
        broken = sorted(
            {
                output.case
                for output in completed
                if output.reward is not None and output.reward != reward
            }
        )
        if broken:
            problems.append(f'{label} {verdict} the tests of {code(broken)}')
        ungraded = sorted(
            {output.case for output in completed if output.reward is None}
        )
        if ungraded:
            problems.append(f'{label} left no verdict on {code(ungraded)}')
        missing = sorted(
            {task.name for task in tasks} - {output.case for output in attempts}
        )
        if missing:
            problems.append(f'{label} left no trial of {code(missing)}')
    return problems


def _family_systems(family: Family, systems: Mapping[str, System]) -> list[System]:
    listed = jobs.listed_systems(family, systems)
    jobs.check_systems(family, listed)
    return jobs.family_systems(family, listed)


def _named_systems(
    family: Family, systems: list[System], names: list[str]
) -> list[System]:
    known_names(
        names,
        [system.name for system in systems],
        f'Family `{family.name}` does not list',
        param_hint="'--system'",
    )
    return [system for system in systems if system.name in names]


def _families_with_jobs() -> list[str]:
    return [name for name in family_names() if jobs.has_jobs(load_family(name))]


def _prepare(
    families: list[str] | None,
    names: list[str] | None,
    export: NixosConfigExport,
    systems: Mapping[str, System],
) -> list[tuple[Family, list[System], JobConfig]]:
    """Return each family to run with its systems and its job."""
    prepared: list[tuple[Family, list[System], JobConfig]] = []
    for family, selected in _run_targets(families, names, systems):
        if not selected:
            raise InputError(
                f'Family `{family.name}` lists no systems. Add them to `systems` in '
                f'`families/{family.name}/family.toml`.'
            )
        _refuse_drift(family, selected)
        tasks = (
            _render_tasks(family)
            if family.source == 'cases'
            else _task_dirs(family, export)
        )
        prepared.append((family, selected, jobs.job(family, selected, tasks)))
    return prepared


def _run_targets(
    families: list[str] | None,
    names: list[str] | None,
    systems: Mapping[str, System],
) -> list[tuple[Family, list[System]]]:
    """Return each family to run with the systems to send it."""
    if families is not None:
        targets: list[tuple[Family, list[System]]] = []
        for name in families:
            family = load_family(name)
            listed = _family_systems(family, systems)
            targets.append(
                (family, _named_systems(family, listed, names) if names else listed)
            )
        return targets

    every = [
        family for family in map(load_family, family_names()) if family.settings.systems
    ]

    if names is None:
        return [(family, _family_systems(family, systems)) for family in every]

    known_names(
        names,
        systems,
        'No family lists',
        f'Check the name against `{SYSTEMS_FILE}`.',
        param_hint="'--system'",
    )
    wanted = known_names(
        names,
        {name for family in every for name in family.settings.systems},
        'No family lists',
        "Add it to `systems` in a family's `family.toml`.",
        param_hint="'--system'",
    )

    targets = []
    for family in every:
        if not set(family.settings.systems) & set(wanted):
            continue
        chosen = [
            system
            for system in _family_systems(family, systems)
            if system.name in wanted
        ]
        targets.append((family, chosen))
    return targets


def _refuse_drift(family: Family, systems: list[System]) -> None:
    drifted = jobs.drifted_systems(family, systems)
    frozen = report.reported_fingerprints(family.name)
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
    raise InputError('\n'.join(lines))


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


def _collect(
    family: Family, systems: dict[str, System], export: NixosConfigExport
) -> list[Output]:
    collected = collection.collect(family, systems)
    outputs = collected.outputs
    errors = Counter(output.error for output in outputs if output.error)
    typer.echo(
        f'Family `{family.name}`: {count_of(len(outputs), "output")}, '
        f'{sum(errors.values())} errored.'
    )
    for error, count in errors.most_common():
        typer.echo(f'  Error `{error}`: {count_of(count, "output")}.')

    for entry in collected.spend:
        typer.echo(_spend_line(entry))

    gated = [output for output in outputs if output.gate_failures]
    if gated:
        typer.echo(
            f'Family `{family.name}`: {count_of(len(gated), "output")} failed a gate.'
        )
        failures = Counter(
            gate for output in gated for gate in output.gate_failures or []
        )
        for gate, count in failures.most_common():
            typer.echo(f'  Gate `{gate}`: {count_of(count, "output")}.')

    rewards = [output.reward for output in outputs if output.reward is not None]
    if rewards:
        typer.echo(f'Mean reward {sum(rewards) / len(rewards):.3f}.')

    graded = {output.case for output in outputs if output.reward is not None}
    solved = {output.case for output in outputs if tests_pass(output.reward)}
    if unsolved := sorted(graded - solved):
        typer.echo(f'No system solved {code(unsolved)}.')

    if family.source == 'tasks' and family.settings.systems:
        _author_rates(family, outputs, _family_systems(family, systems), export)

    versions = sorted(
        {output.harness_version for output in outputs if output.harness_version}
    )
    if len(versions) > 1:
        typer.echo(f'The outputs span Harbor versions {code(versions)}.')

    return outputs


def _echo_conflicts(family: Family, outputs: list[Output]) -> None:
    for conflict in pool_conflicts(family, outputs):
        typer.echo(conflict)


def _spend_line(entry: Spend) -> str:
    attempts = (
        f'{count_of(entry.trials, "trial")} and {count_of(entry.responses, "response")}'
    )
    if not entry.priced:
        line = (
            f'System `{entry.system}` is unpriced: {attempts} used '
            f'{entry.tokens.input_tokens:,.0f} input and '
            f'{entry.tokens.output_tokens:,.0f} output tokens'
        )
        if entry.unknown:
            line += f', besides {count_of(entry.unknown, "unmeasured response")}'
        return f'{line}.'

    line = f'System `{entry.system}`: {attempts} cost ${entry.known_usd:.2f}'
    for basis, share in sorted(entry.imputed.items()):
        line += (
            f', plus ${share.usd:.2f} for {count_of(share.responses, "response")} '
            f'without a known cost, imputed from {USAGE_SOURCES[basis]} usage'
        )
    if entry.ceiling.responses:
        ceiling = 'its ceiling' if entry.ceiling.responses == 1 else 'their ceilings'
        line += (
            f', besides {count_of(entry.ceiling.responses, "response")} without a '
            f'known cost or usage to impute from, ${entry.ceiling.usd:.2f} at '
            f'{ceiling}, which the budget leaves out'
        )
    return f'{line}.'


def _projection(
    family: Family,
    selected: list[System],
    job: JobConfig,
    systems: Mapping[str, System],
) -> tuple[list[Spend], list[Projection]]:
    spent = summarize(update_ledger(family, systems))
    return spent, project(jobs.pending_trials(job), spent, selected)


def _echo_projection(
    family: Family, spent: list[Spend], projections: list[Projection]
) -> None:
    for projection in projections:
        if not projection.pending:
            continue
        name = f'System `{projection.system}`'
        trials = count_of(projection.pending, 'pending trial')
        if projection.per_trial_usd is not None and projection.basis is not None:
            typer.echo(
                f'{name}: {trials} at ${projection.per_trial_usd:.2f} each from '
                f'{USAGE_SOURCES[projection.basis]} usage, '
                f'${projection.usd:.2f} in all.'
            )
        elif not projection.priced and projection.tokens is not None:
            typer.echo(
                f'{name} is unpriced: {trials} of about '
                f'{projection.tokens.input_tokens:,.0f} input and '
                f'{projection.tokens.output_tokens:,.0f} output tokens.'
            )
        elif projection.priced and family.settings.budget_usd is not None:
            typer.echo(
                f'{name}: {trials} with no usage to estimate a cost from, so this '
                'run can exceed the budget.'
            )
        else:
            typer.echo(f'{name}: {trials} with no usage to estimate a cost from.')

    total = total_usd(spent, projections)
    budget = family.settings.budget_usd
    typer.echo(
        f'Family `{family.name}`: ${total:.2f} spent and projected'
        + (f', against a budget of ${budget:.2f}.' if budget is not None else '.')
    )


def _author_rates(
    family: Family,
    outputs: list[Output],
    systems: list[System],
    export: NixosConfigExport,
) -> None:
    tasks = [case_task(family, case.id) for case in load_cases(family)]
    vendors = author_vendors(family, tasks, export)

    for system in systems:
        rates: list[str] = []
        for own, label in ((True, 'own-vendor'), (False, 'other-vendor')):
            trials = [
                output
                for output in outputs
                if output.system == system.name
                and (vendors[output.case] == system.vendor) == own
            ]
            passed = sum(tests_pass(output.reward) for output in trials)
            rate = (
                f'{passed}/{len(trials)} ({passed / len(trials):.1%})'
                if trials
                else 'no trials'
            )
            rates.append(f'{label} tasks {rate}')

        typer.echo(f'System `{system.name}`: pass rate on {", ".join(rates)}.')


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
