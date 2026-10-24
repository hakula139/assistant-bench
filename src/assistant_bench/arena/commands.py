"""Reviewed artifact publication and private native arena management."""

import asyncio
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from pydantic import TypeAdapter

from assistant_bench.arena.exports import capture_export, read_export, write_export
from assistant_bench.arena.management import Management, connect
from assistant_bench.arena.preparation import Preparation, prepare_selection
from assistant_bench.arena.publication import public_catalog
from assistant_bench.arena.publishing import publish_points, publish_release
from assistant_bench.arena.runtime import (
    TargetName,
    deploy as deploy_target,
    load_target,
    management_config,
)
from assistant_bench.arena.snapshots import analyze_export
from assistant_bench.paths import REPORTS_DIR, write_atomically
from assistant_bench_arena import management_domain as command
from assistant_bench_arena.investigation import (
    FamilyStatus,
    InvestigationPage,
    ReceiptFilter,
)
from assistant_bench_arena.ledger_models import (
    MODERATION_CHUNK_SIZE,
    DeploymentStatus,
    Event,
    Export,
    Moderation,
)
from assistant_bench_arena.models import FamilyView
from assistant_bench_arena.releases import Release
from assistant_bench_arena.serialization import digest
from assistant_bench_arena.transport import CommandRejected


app = typer.Typer(no_args_is_help=True)
_TARGET = TypeAdapter[TargetName](TargetName.__value__)
_EXPORT = TypeAdapter(Export)
_STATUS = TypeAdapter(DeploymentStatus | None)
_FAMILY = TypeAdapter(FamilyStatus | None)
_INVESTIGATION = TypeAdapter(InvestigationPage)
_EVENT = TypeAdapter(Event)
_NONE = TypeAdapter(type(None))
_CATALOG = TypeAdapter(tuple[FamilyView, ...])

Target = Annotated[str, typer.Argument(help='Configured staging or production target.')]
Artifact = Annotated[
    Path, typer.Argument(help='A reviewed immutable release JSON file.')
]


@asynccontextmanager
async def _connection(name: str) -> AsyncGenerator[Management]:
    target = load_target(_TARGET.validate_python(name))

    with management_config(target) as config:
        async with connect(config) as management:
            status = await management.domain(command.Status(), _STATUS)

            if status is None or status.namespace != target.profile.namespace:
                raise ValueError(
                    'The connected arena does not match the selected target.'
                )

            yield management


def _run(operation: Coroutine[None, None, None]) -> None:
    try:
        asyncio.run(operation)
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    except CommandRejected as error:
        typer.echo(f'The arena refused the command: `{error.reason}`.', err=True)
        raise typer.Exit(1) from error


@app.command()
def deploy(target: TargetName) -> None:
    """Deploy the prepared site and voting services to a configured target."""
    try:
        deploy_target(load_target(target))
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    typer.echo(f'Deployed voting services for `{target}`.')


@app.command()
def prepare(
    selection: Annotated[
        Path,
        typer.Argument(help='Exact selected solutions and public approval JSON.'),
    ],
    output: Annotated[
        Path, typer.Argument(help='The private prepared release JSON file.')
    ],
) -> None:
    """Prove selected current task inputs and prepare an exact reviewed release."""
    try:
        _check_output(output)
        release = prepare_selection(
            Preparation.model_validate_json(selection.read_bytes())
        )
        write_atomically(output, release.model_dump_json(indent=2) + '\n')
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    typer.echo(f'Prepared release written to `{output}`.')


@app.command()
def publish(target: Target, release: Artifact) -> None:
    """Stage and activate an approved release without resetting fitted scores."""
    _run(_publish(target, release))


async def _publish(target: str, path: Path) -> None:
    release = Release.model_validate_json(path.read_bytes())

    async with _connection(target) as management:
        event = await publish_release(management, release)

    if event is None:
        typer.echo('The approved release is already active.')
    else:
        typer.echo(
            f'Published `{release.evaluation.family}` at revision `{event.seq}`.'
        )


@app.command(name='export')
def export_evidence(
    target: Target,
    family: Annotated[str, typer.Argument(help='An active arena family.')],
    output: Annotated[
        Path, typer.Argument(help='The private evidence file to create.')
    ],
) -> None:
    """Capture a registered immutable evidence cut through bounded native pages."""
    _run(_export(target, family, output))


async def _export(target: str, family: str, output: Path) -> None:
    _check_output(output)

    if output.exists():
        raise ValueError('The evidence output already exists.')

    async with _connection(target) as management:
        header = await management.domain(
            command.Freeze(export_id=uuid4().hex, family=family), _EXPORT
        )
        capture = await capture_export(management, header)

    write_export(capture, output)
    typer.echo(f'Evidence written to `{output}`.')


@app.command()
def refresh(
    target: Target,
    snapshot: Annotated[Path, typer.Argument(help='A saved ledger evidence cut.')],
) -> None:
    """Fit frozen evidence offline and publish the resulting sampling points."""
    _run(_refresh(target, snapshot))


async def _refresh(target: str, path: Path) -> None:
    frozen = analyze_export(read_export(path))

    async with _connection(target) as management:
        event = await publish_points(
            management, frozen.analysis.family.name, frozen.points
        )

    if event is None:
        typer.echo('The fitted sampling points are already active.')
    else:
        typer.echo(f'Sampling points activated at revision `{event.seq}`.')


@app.command()
def status(
    target: Target,
    family: Annotated[str, typer.Argument(help='An active arena family.')],
) -> None:
    """Show active identities, sampling provenance and current raw coverage."""
    _run(_status(target, family))


async def _status(target: str, family: str) -> None:
    async with _connection(target) as management:
        current = await management.domain(command.FamilyStatus(family=family), _FAMILY)

    if current is None:
        raise ValueError(f'Family `{family}` is not active.')

    typer.echo(current.model_dump_json(indent=2))


@app.command()
def investigate(
    target: Target,
    filters: Annotated[Path, typer.Argument(help='Private receipt filter JSON.')],
    after: Annotated[
        int, typer.Option(min=0, help='Continue after this receipt sequence.')
    ] = 0,
) -> None:
    """Inspect one bounded page of receipts and searchable network associations."""
    _run(_investigate(target, filters, after))


async def _investigate(target: str, path: Path, after: int) -> None:
    filters = ReceiptFilter.model_validate_json(path.read_bytes())

    async with _connection(target) as management:
        page = await management.domain(
            command.Investigate(filters=filters, after=after), _INVESTIGATION
        )

    typer.echo(page.model_dump_json(indent=2))


@app.command()
def moderate(
    target: Target,
    plan: Annotated[Path, typer.Argument(help='Reviewed moderation plan JSON.')],
) -> None:
    """Apply or resume the exact reviewed exclusion or restoration plan."""
    _run(_moderate(target, plan))


async def _moderate(target: str, path: Path) -> None:
    plan = Moderation.model_validate_json(path.read_bytes())

    async with _connection(target) as management:
        await management.domain(command.StageModeration(batch=plan), _NONE)
        await management.domain(command.SealModeration(batch_id=plan.id), _NONE)

        for chunk in range(
            (len(plan.targets) + MODERATION_CHUNK_SIZE - 1) // MODERATION_CHUNK_SIZE
        ):
            request = command.Moderate(
                command_id=digest(
                    {'operation': 'moderate', 'plan': plan.id, 'chunk': chunk}
                ),
                batch_id=plan.id,
                chunk_index=chunk,
            )
            await management.domain(request, _EVENT)

    typer.echo(f'Moderation plan `{plan.id}` applied.')


@app.command()
def catalog(
    output: Annotated[
        Path, typer.Argument(help='The generated public family catalog.')
    ],
    releases: Annotated[
        list[Path], typer.Argument(help='Approved public release JSON files.')
    ],
) -> None:
    """Write the safe family labels served by the public voting application."""
    try:
        _check_output(output)
        families = public_catalog(
            [Release.model_validate_json(path.read_bytes()) for path in releases]
        )
        write_atomically(output, _CATALOG.dump_json(families, indent=2).decode() + '\n')
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    typer.echo(f'Public family catalog written to `{output}`.')


def _check_output(path: Path) -> None:
    if path.resolve().is_relative_to(REPORTS_DIR.resolve()):
        raise ValueError('Only `bench close` writes under reports/.')
