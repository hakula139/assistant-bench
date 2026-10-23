"""The `bench` command."""

import os
import subprocess
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import tomli_w
import typer
from dotenv import find_dotenv, load_dotenv
from harbor.models.job.config import JobConfig

from assistant_bench import jobs, report, revisions
from assistant_bench.arena import api
from assistant_bench.arena.service import Arena, load_families
from assistant_bench.arena.store import VoteStore, votes_file
from assistant_bench.catalog import System, load_export, load_systems, pin
from assistant_bench.families import (
    Family,
    VotesSettings,
    family_names,
    load_cases,
    load_family,
)
from assistant_bench.paths import REPO_ROOT


LOCAL_SITE_VAR = 'BENCH_LOCAL'
SITE_DIR = REPO_ROOT / 'web' / 'dist'

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run families on the systems under test, vote on their outputs, and report."""
    load_dotenv(find_dotenv(usecwd=True))


@app.command()
def check(
    families: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Validate every family, or the named ones, running Harbor's oracle on tasks."""
    for name in _picked(families, family_names):
        selected = _load_family(name)
        _family_systems(selected)
        if selected.source == 'cases':
            typer.echo(f'{name}: {len(_render_tasks(selected))} valid cases')
            continue

        try:
            job = jobs.oracle_job(selected)
        except ValueError as error:
            raise typer.BadParameter(str(error), param_hint='FAMILY') from error
        outputs = jobs.read_trials(selected, _run(job), {})
        failed = sorted(
            {output.case for output in outputs if output.error or output.reward != 1}
        )
        tasks = {task.get_task_id().get_name() for task in job.tasks}
        missing = sorted(tasks - {output.case for output in outputs})
        if failed:
            typer.echo(f'{name}: the oracle failed on {", ".join(failed)}', err=True)
        if missing:
            typer.echo(
                f'{name}: the oracle left no trial of {", ".join(missing)}', err=True
            )
        if failed or missing:
            raise typer.Exit(1)
        typer.echo(f'{name}: the oracle passed every task')


@app.command()
def run(
    families: Annotated[list[str] | None, typer.Argument()] = None,
    names: Annotated[
        list[str] | None,
        typer.Option(
            '--system',
            help='Limit the run to this system. Without a family, this sends it '
            'to every family that lists it. Repeat for several.',
        ),
    ] = None,
) -> None:
    """Generate missing trials and collect them for every family, or the named ones.

    A run without a family sends every family that lists systems.
    """
    prepared = []
    for family, systems in _run_targets(families, names):
        if family.source != 'cases':
            raise typer.BadParameter(
                'single-call systems run only case families', param_hint='FAMILY'
            )
        _refuse_drift(family, systems)
        tasks = _render_tasks(family)
        prepared.append((family, systems, tasks))

    for family, systems, tasks in prepared:
        try:
            path = jobs.run_job(jobs.case_job(family, systems, tasks))
        except subprocess.CalledProcessError as error:
            _ingest(family)
            raise typer.Exit(error.returncode) from error
        typer.echo(f'Job written to {path}')
        _ingest(family)


@app.command()
def ingest(
    families: Annotated[list[str] | None, typer.Argument()] = None,
) -> None:
    """Collect every family's trials into `outputs/<family>.jsonl`."""
    names = _picked(families, _families_with_jobs)
    if not names:
        typer.echo('No family has jobs yet')
        return
    for name in names:
        _ingest(_load_family(name))


def _vote_store() -> VoteStore:
    """Open the vote store, reporting a schema mismatch."""
    try:
        return VoteStore(votes_file())
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


@app.command()
def serve() -> None:
    """Collect every votes family's trials, then serve the vote page."""
    for name in family_names():
        family = _load_family(name)
        if isinstance(family.settings, VotesSettings) and jobs.has_jobs(family):
            _ingest(family)
    families = load_families()
    if not families:
        typer.echo('No family scored by votes has outputs yet', err=True)
        raise typer.Exit(1)
    with closing(_vote_store()) as store:
        try:
            subprocess.run(
                ['pnpm', '--filter', 'web', 'build'],
                check=True,
                cwd=REPO_ROOT,
                env={**os.environ, LOCAL_SITE_VAR: '1'},
            )
        except subprocess.CalledProcessError as error:
            raise typer.Exit(error.returncode) from error
        page = SITE_DIR / api.VOTE_DIR / 'index.html'
        if not page.is_file():
            typer.echo(
                f'No vote page at {page}. Build the site with '
                f'`{LOCAL_SITE_VAR}=1 pnpm --filter web build` and fix what it '
                'reports.',
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(
            f'Voting on {", ".join(families)} at {api.APP_ORIGIN}/{api.VOTE_DIR}/'
        )
        api.serve(Arena(store, families, api.RENDER_ORIGIN), SITE_DIR)


@app.command()
def close(family: str) -> None:
    """Collect the family's trials and write its report under `reports/`."""
    selected = _load_family(family)
    with closing(_vote_store()) as store:
        _ingest(selected)
        try:
            closed = report.close(selected, store, datetime.now(UTC))
        except ValueError as error:
            raise typer.BadParameter(str(error), param_hint='FAMILY') from error
    typer.echo(f'Report written to {report.write_report(closed)}')


@app.command()
def fingerprints(base: str) -> None:
    """Fail when a system keeps its name but its fingerprint differs from BASE.

    Print each changed system's table at BASE, which pins the old resolution.
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
                f'system {name!r} changed its fingerprint since {base}, so give '
                'the changed system a new name. A prompt system cannot be pinned '
                'with a `route` table yet',
                err=True,
            )
            continue
        typer.echo(
            f'system {name!r} changed its fingerprint since {base}, so give the '
            'changed system a new name, and keep the old name on its old '
            'resolution with the table below, as the Systems section of '
            'docs/guide/authoring.md describes',
            err=True,
        )
        typer.echo(tomli_w.dumps({name: config.model_dump(exclude_none=True)}))
        typer.echo()
    if changed:
        raise typer.Exit(1)
    typer.echo(f'Every system keeps its fingerprint since {base}')


def _load_family(name: str) -> Family:
    try:
        return load_family(name)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint='FAMILY') from error


def _load_systems() -> dict[str, System]:
    try:
        return load_systems(load_export())
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint='systems.toml') from error


def _render_tasks(family: Family) -> list[Path]:
    try:
        return jobs.render_tasks(family, load_cases(family))
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint='FAMILY') from error


def _family_systems(family: Family) -> list[System]:
    if not family.settings.systems:
        return []
    known = _load_systems()
    unknown = [name for name in family.settings.systems if name not in known]
    if unknown:
        raise typer.BadParameter(
            f'{family.name} lists unknown systems: {", ".join(unknown)}',
            param_hint='FAMILY',
        )
    return [known[name] for name in family.settings.systems]


def _named_systems(
    family: Family, systems: list[System], names: list[str]
) -> list[System]:
    listed = [system.name for system in systems]
    unknown = [name for name in dict.fromkeys(names) if name not in listed]
    if unknown:
        raise typer.BadParameter(
            f'{family.name} does not list {", ".join(unknown)}, '
            f'so choose among {", ".join(listed)}',
            param_hint='--system',
        )
    return [system for system in systems if system.name in names]


def _picked(families: list[str] | None, every: Callable[[], list[str]]) -> list[str]:
    return list(dict.fromkeys(families)) if families is not None else every()


def _families_with_jobs() -> list[str]:
    return [name for name in family_names() if jobs.has_jobs(_load_family(name))]


def _run_targets(
    families: list[str] | None, names: list[str] | None
) -> list[tuple[Family, list[System]]]:
    """Return each family to run with the systems to send it."""
    if families is not None:
        targets = []
        for name in dict.fromkeys(families):
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
            f'no family lists {", ".join(unlisted)}', param_hint='--system'
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
        raise typer.BadParameter(str(error), param_hint='FAMILY') from error
    if not drifted:
        return
    detail = '\n'.join(
        f'{name!r} ran under fingerprint {recorded} but now resolves to {current}'
        for name, recorded, current in drifted
    )
    raise typer.BadParameter(
        f'{family.name}:\n{detail}\nGive a changed system a new name, or keep its '
        'old resolution on a `route` table, as docs/guide/authoring.md#systems '
        'describes',
        param_hint='systems.toml',
    )


def _ingest(family: Family) -> None:
    outputs = jobs.ingest(family, _load_systems())
    typer.echo(f'{family.name}:')
    errors = Counter(output.error for output in outputs if output.error)
    typer.echo(f'{len(outputs)} outputs, {sum(errors.values())} errored')
    for error, count in errors.most_common():
        typer.echo(f'  {error}: {count}')
    gated = [output for output in outputs if output.gate_failures]
    if gated:
        typer.echo(f'{len(gated)} failed a gate')
        failures = Counter(
            gate for output in gated for gate in output.gate_failures or []
        )
        for gate, count in failures.most_common():
            typer.echo(f'  {gate}: {count}')
    rewards = [output.reward for output in outputs if output.reward is not None]
    if rewards:
        typer.echo(f'Mean reward {sum(rewards) / len(rewards):.3f}')


def _run(job: JobConfig) -> Path:
    try:
        return jobs.run_job(job)
    except subprocess.CalledProcessError as error:
        raise typer.Exit(error.returncode) from error
