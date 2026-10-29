"""Publication and inspection of the arena's vote stores."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import TypeAdapter

from assistant_bench import jobs
from assistant_bench.arena import publication, votes
from assistant_bench.arena.d1 import D1Error, Target, connect
from assistant_bench.arena.runtime import (
    TargetName,
    deploy as deploy_target,
    load_target,
)
from assistant_bench.catalog import load_export, load_systems
from assistant_bench.families import load_cases, load_family


app = typer.Typer(no_args_is_help=True)
_TARGET = TypeAdapter[TargetName](TargetName.__value__)

FAMILY_HELP = 'A directory name under families/, such as writing-zh.'


@app.command()
def deploy(
    target: Annotated[
        str, typer.Argument(help='Configured staging or production target.')
    ],
) -> None:
    """Deploy the prepared site and voting services to a configured target."""
    try:
        deploy_target(load_target(_TARGET.validate_python(target)))
    except (OSError, ValueError) as error:
        _fail(str(error))

    typer.echo(f'Deployed voting services for `{target}`.')


@app.command()
def prepare(
    family: Annotated[str, typer.Argument(metavar='FAMILY', help=FAMILY_HELP)],
) -> None:
    """Write the family's production publication from its current outputs.

    The file goes to publications/<family>/ in the data directory with a Markdown
    listing beside it. Reviewing the listing and publishing that exact file
    approves it.
    """
    try:
        selected = load_family(family)
        systems = load_systems(load_export())
        listed = jobs.listed_systems(selected, systems)
        outputs = jobs.collect(selected, systems)
        prepared = publication.prepare(
            selected,
            load_cases(selected),
            outputs,
            {system.name: system.display_name for system in listed},
            'production',
        )
        path = publication.write(prepared, outputs)
    except (OSError, RuntimeError, ValueError) as error:
        _fail(str(error))

    typer.echo(
        f'Publication of `{family}` with {len(prepared.cases)} cases and '
        f'{len(prepared.outputs)} outputs written to `{path}`. Review '
        f'`{path.with_suffix(".md")}`, then publish that file.'
    )


@app.command()
def publish(
    target: Target,
    file: Annotated[
        Path, typer.Argument(help='A reviewed publication written by `prepare`.')
    ],
) -> None:
    """Stage a reviewed publication in the vote store and activate it."""
    try:
        prepared = publication.read(file)
        with connect(target) as database:
            changed = publication.publish(database, prepared, datetime.now(UTC))
    except (D1Error, OSError, ValueError) as error:
        _fail(str(error))

    if not changed:
        typer.echo(
            f'Publication `{prepared.id}` is already active for `{prepared.family}` '
            f'in `{target}`, so nothing changed.'
        )
        return

    typer.echo(
        f'Published `{prepared.family}` to `{target}`: publication `{prepared.id}` '
        f'with {len(prepared.cases)} cases and {len(prepared.outputs)} outputs.'
    )


@app.command()
def status(
    target: Target,
    family: Annotated[
        str | None, typer.Argument(metavar='[FAMILY]', help=FAMILY_HELP)
    ] = None,
) -> None:
    """Show each active family's publication and votes, or only the named family's."""
    try:
        with connect(target) as database:
            rows = votes.status(database, family)
    except (D1Error, OSError, ValueError) as error:
        _fail(str(error))

    if not rows:
        if family is not None:
            _fail(f'Family `{family}` has no active publication in `{target}`.')
        typer.echo(f'No family has an active publication in `{target}`.')

    for row in rows:
        latest = 'none' if row.latest_vote_at is None else _time(row.latest_vote_at)
        typer.echo(
            f'Family `{row.family}`: publication `{row.publication_id}` active since '
            f'{_time(row.activated_at)}, with {row.case_count} cases and '
            f'{row.output_count} outputs. {row.votes} counted votes from '
            f'{row.voters} voters, {row.excluded} excluded, latest at {latest}.'
        )


def _time(milliseconds: int) -> str:
    return f'{datetime.fromtimestamp(milliseconds / 1000, UTC):%Y-%m-%d %H:%M} UTC'


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)
