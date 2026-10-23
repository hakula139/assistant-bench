"""The `bench` command."""

import os
import subprocess
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import tomli_w
import typer
from dotenv import find_dotenv, load_dotenv
from harbor.models.job.config import JobConfig

from assistant_bench import jobs, report, revisions
from assistant_bench.arena import api
from assistant_bench.arena.service import Arena, load_families
from assistant_bench.arena.store import VoteStore, votes_file
from assistant_bench.catalog import (
    SYSTEMS_FILE,
    System,
    load_export,
    load_systems,
    pin,
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
from assistant_bench.messages import code
from assistant_bench.outputs import Output
from assistant_bench.paths import REPO_ROOT


LOCAL_SITE_VAR = 'BENCH_LOCAL'
SITE_DIR = REPO_ROOT / 'web' / 'dist'
SYSTEMS_DOC = 'docs/guide/authoring.md#systems'
HARBOR_DOC = 'docs/research/harbor.md#quirks-of-the-pinned-release'

app = typer.Typer(no_args_is_help=True)


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


def _known_family(name: str) -> str:
    _known_families([name])
    return name


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
    os.environ.update(jobs.HARBOR_ENV)


@app.command()
def check(families: FamilyNames = None) -> None:
    """Validate every family, or the named ones.

    For a task family, also run each task's reference solution and a do-nothing
    agent in Docker, several times each, so the solution must pass the task's
    tests and the unsolved task must fail them.
    """
    task_families = []
    for family in map(_load_family, families or family_names()):
        _family_systems(family)
        if family.source == 'cases':
            cases = len(_render_tasks(family))
            typer.echo(f'Family `{family.name}`: {cases} valid cases.')
            continue
        task_families.append((family, _task_dirs(family)))

    failed = False
    for family, tasks in task_families:
        outputs = _read_trials(family, _run(jobs.check_job(family, tasks)))
        problems = _check_problems(tasks, outputs)
        for problem in problems:
            typer.echo(f'Family `{family.name}`: {problem}.', err=True)
        if not problems:
            typer.echo(
                f'Family `{family.name}`: every reference solution passed its tests, '
                'and the do-nothing agent failed every task.'
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
    prepared = []
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
def serve() -> None:
    """Collect every family scored by votes, then serve the vote page until stopped."""
    for name in family_names():
        family = _load_family(name)
        if isinstance(family.settings, VotesSettings) and jobs.has_jobs(family):
            _collect(family)
    families = load_families()
    if not families:
        _fail(
            'No family scored by votes has outputs yet. Run '
            '`uv run bench run <family>` first.'
        )
    os.environ[LOCAL_SITE_VAR] = '1'
    with closing(_vote_store()) as store:
        try:
            subprocess.run(
                ['pnpm', '--filter', 'web', 'build'], check=True, cwd=REPO_ROOT
            )
        except subprocess.CalledProcessError as error:
            typer.echo(
                'Building the vote page failed. If the checkout lacks node_modules, '
                'run `pnpm install` once, then rerun `uv run bench serve`.',
                err=True,
            )
            raise typer.Exit(error.returncode) from error
        page = SITE_DIR / api.VOTE_DIR / 'index.html'
        if not page.is_file():
            _fail(
                f'No vote page at `{page}`. Build the site with '
                f'`{LOCAL_SITE_VAR}=1 pnpm --filter web build` and fix what it '
                'reports.'
            )
        typer.echo(
            f'Voting on {code(families)} at {api.APP_ORIGIN}/{api.VOTE_DIR}/. '
            'Open that exact address, since the server refuses `localhost`.'
        )
        api.serve(Arena(store, families, api.RENDER_ORIGIN), SITE_DIR)


@app.command()
def close(
    family: Annotated[
        str, typer.Argument(metavar='FAMILY', help=FAMILY_HELP, callback=_known_family)
    ],
) -> None:
    """Collect the family's trials and write its report to reports/ in the checkout.

    The report is reports/<family>/<UTC date>.json, replacing one closed earlier the
    same day.
    """
    selected = _load_family(family)
    with closing(_vote_store()) as store:
        _collect(selected)
        try:
            closed = report.close(selected, store, datetime.now(UTC))
        except ValueError as error:
            _fail(str(error))
    typer.echo(f'Report written to `{report.write_report(closed)}`.')


@app.command()
def fingerprints(
    base: Annotated[
        str, typer.Argument(metavar='BASE', help='A Git revision, such as main.')
    ],
) -> None:
    """Fail when a system kept its name since BASE but resolves to a new fingerprint.

    The fingerprint hashes a system's route, effort, harness, and prompt. For each
    changed name, print the systems.toml table that pins its BASE resolution. Nix
    builds BASE's nixos-config export.
    """
    try:
        base_systems = revisions.systems_at(base)
    except subprocess.CalledProcessError as error:
        raise typer.Exit(error.returncode) from error
    changed = revisions.changed_fingerprints(base_systems, _load_systems())
    for name in changed:
        system = base_systems[name]
        try:
            config = pin(system)
        except ValueError:
            typer.echo(
                f'System `{name}` changed its fingerprint since `{base}`, and a '
                '`route` table cannot pin its prompt. Give the changed system a '
                'new name and keep the old name only in closed reports, as '
                f'`{SYSTEMS_DOC}` describes.',
                err=True,
            )
            continue
        typer.echo(
            f'System `{name}` changed its fingerprint since `{base}`. Give the '
            'changed system a new name, and keep the old name on its old '
            f'resolution with the table below, as `{SYSTEMS_DOC}` describes.',
            err=True,
        )
        typer.echo(tomli_w.dumps({name: config.model_dump(exclude_none=True)}))
        typer.echo()
    if changed:
        raise typer.Exit(1)
    typer.echo(f'Every system keeps its fingerprint since `{base}`.')


# ── Helpers ──


def _fail(message: str) -> NoReturn:
    """Report a problem with the checkout or the data directory, and exit."""
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
    except ValueError as error:
        _fail(str(error))
    changed = _changed_tasks(family, tasks)
    if changed:
        detail = '\n'.join(
            f'Task `{task}` changed since a trial ran it at version `{version}`.'
            for task, version in changed
        )
        _fail(
            f'Family `{family.name}` has tasks to bump:\n{detail}\nBump each '
            "task's `[task] version`: the patch for a change no trial depends on, "
            'such as its solution, the minor for a change to `tests/` alone, '
            'which regrades its trials, or the major for anything the agent '
            'sees, which reruns them.'
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
    listed = []
    if family.settings.systems:
        known = _load_systems()
        unknown = [name for name in family.settings.systems if name not in known]
        if unknown:
            _fail(
                f'Family `{family.name}` lists {code(unknown)}, which '
                f'`{SYSTEMS_FILE}` does not define. Add a table to '
                f'`{SYSTEMS_FILE}` or fix the name in '
                f'`families/{family.name}/family.toml`.'
            )
        listed = [known[name] for name in family.settings.systems]
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
        targets = []
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
    except ValueError as error:
        _fail(str(error))
    if not drifted:
        return
    detail = '\n'.join(
        f'System `{name}` ran under fingerprint `{recorded}` but now resolves to '
        f'`{current}`.'
        for name, recorded, current in drifted
    )
    _fail(
        f'Family `{family.name}` has systems that changed their fingerprints:\n'
        f'{detail}\nGive a changed system a new name, or keep its old resolution '
        f'on a `route` table, as `{SYSTEMS_DOC}` describes.'
    )


def _collect(family: Family) -> None:
    try:
        outputs = jobs.collect(family, _load_systems())
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
    versions = sorted(
        {output.harness_version for output in outputs if output.harness_version}
    )
    if len(versions) > 1:
        typer.echo(f'The outputs span Harbor versions {code(versions)}.')


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


def _vote_store() -> VoteStore:
    """Open the vote store, reporting a schema mismatch."""
    try:
        return VoteStore(votes_file())
    except ValueError as error:
        _fail(str(error))
