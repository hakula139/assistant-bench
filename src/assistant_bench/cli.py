"""The `bench` command."""

import os
import subprocess
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv
from harbor.models.job.config import JobConfig

from assistant_bench import jobs, report, revisions
from assistant_bench.arena import api
from assistant_bench.arena.service import Arena, load_families
from assistant_bench.arena.store import VoteStore, votes_file
from assistant_bench.catalog import System, load_export, load_systems
from assistant_bench.families import Family, load_cases, load_family
from assistant_bench.paths import REPO_ROOT


LOCAL_SITE_VAR = 'BENCH_LOCAL'
SITE_DIR = REPO_ROOT / 'web' / 'dist'

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run families on the systems under test, vote on their outputs, and report."""
    load_dotenv(find_dotenv(usecwd=True))


@app.command()
def check(family: str) -> None:
    """Validate a family, running Harbor's oracle on its task directories."""
    selected = _load_family(family)
    _family_systems(selected)
    if selected.source == 'cases':
        typer.echo(f'{family}: {len(_render_tasks(selected))} valid cases')
        return

    job_dir = _run(jobs.oracle_job(selected))
    failed = sorted(
        {
            output.case
            for output in jobs.read_trials(selected, job_dir, {})
            if output.error or output.reward != 1
        }
    )
    if failed:
        typer.echo(f'{family}: the oracle failed on {", ".join(failed)}', err=True)
        raise typer.Exit(1)
    typer.echo(f'{family}: the oracle passed every task')


@app.command()
def run(
    family: str,
    names: Annotated[
        list[str] | None,
        typer.Option(
            '--system',
            help="Limit the run to this system from the family's list. "
            'Repeat for several.',
        ),
    ] = None,
) -> None:
    """Generate missing trials for every listed system, or only for the named ones."""
    selected = _load_family(family)
    if selected.source != 'cases':
        raise typer.BadParameter(
            'single-call systems run only case families', param_hint='FAMILY'
        )
    systems = _family_systems(selected)
    if not systems:
        raise typer.BadParameter(f'{family} lists no systems', param_hint='FAMILY')
    if names:
        systems = _named_systems(selected, systems, names)

    tasks = _render_tasks(selected)
    typer.echo(f'Job written to {_run(jobs.case_job(selected, systems, tasks))}')


@app.command()
def ingest(family: str) -> None:
    """Collect every trial of a family into `outputs/<family>.jsonl`."""
    outputs = jobs.ingest(_load_family(family), load_systems(load_export()))
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


@app.command()
def serve() -> None:
    """Build the local vote page and serve it for every family with outputs."""
    families = load_families()
    if not families:
        typer.echo('No family scored by votes has outputs yet', err=True)
        raise typer.Exit(1)
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
            f'`{LOCAL_SITE_VAR}=1 pnpm --filter web build` and fix what it reports.',
            err=True,
        )
        raise typer.Exit(1)
    with closing(VoteStore(votes_file())) as store:
        typer.echo(
            f'Voting on {", ".join(families)} at {api.APP_ORIGIN}/{api.VOTE_DIR}/'
        )
        api.serve(Arena(store, families, api.RENDER_ORIGIN), SITE_DIR)


@app.command()
def close(family: str) -> None:
    """Write the family's report to `reports/<family>/<round>.json`."""
    selected = _load_family(family)
    try:
        with closing(VoteStore(votes_file())) as store:
            closed = report.close(selected, store, datetime.now(UTC))
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint='FAMILY') from error
    typer.echo(f'Report written to {report.write_report(closed)}')


@app.command()
def fingerprints(base: str) -> None:
    """Fail when a system keeps its name but its fingerprint differs from BASE."""
    changed = revisions.changed_fingerprints(
        revisions.systems_at(base), load_systems(load_export())
    )
    for name in changed:
        typer.echo(
            f'system {name!r} changed its fingerprint since {base}, '
            'so give the changed system a new name',
            err=True,
        )
    if changed:
        raise typer.Exit(1)
    typer.echo(f'Every system keeps its fingerprint since {base}')


def _load_family(name: str) -> Family:
    try:
        return load_family(name)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint='FAMILY') from error


def _render_tasks(family: Family) -> list[Path]:
    try:
        return jobs.render_tasks(family, load_cases(family))
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint='FAMILY') from error


def _family_systems(family: Family) -> list[System]:
    if not family.settings.systems:
        return []
    known = load_systems(load_export())
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


def _run(job: JobConfig) -> Path:
    try:
        return jobs.run_job(job)
    except subprocess.CalledProcessError as error:
        raise typer.Exit(error.returncode) from error
