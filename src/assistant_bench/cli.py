"""The `bench` command."""

import subprocess
from collections import Counter
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv
from harbor.models.job.config import JobConfig

from assistant_bench import jobs, revisions
from assistant_bench.catalog import System, load_export, load_systems
from assistant_bench.families import Family, load_cases, load_family


app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run families on the systems under test and collect their outputs."""
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
def run(family: str) -> None:
    """Generate the family's missing trials for every system it lists."""
    selected = _load_family(family)
    if selected.source != 'cases':
        raise typer.BadParameter(
            'single-call systems run only case families', param_hint='FAMILY'
        )
    systems = _family_systems(selected)
    if not systems:
        raise typer.BadParameter(f'{family} lists no systems', param_hint='FAMILY')

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


def _run(job: JobConfig) -> Path:
    try:
        return jobs.run_job(job)
    except subprocess.CalledProcessError as error:
        raise typer.Exit(error.returncode) from error
