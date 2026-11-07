"""Publication, inspection and moderation of the arena's vote stores."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Literal

import typer
from pydantic import TypeAdapter, ValidationError

from assistant_bench.arena import d1, publication, votes
from assistant_bench.arguments import FamilyArgument, OptionalFamilyArgument
from assistant_bench.database import Database
from assistant_bench.families import Choice
from assistant_bench.messages import InputError, code, count_of, error_lines
from assistant_bench.outputs import Output, read_outputs
from assistant_bench.runtime import open_context
from assistant_bench.state import State


app = typer.Typer(no_args_is_help=True)

CHOICE_LABELS: dict[Choice, str] = {
    'a': 'A',
    'b': 'B',
    'tie': 'Tie',
    'both_bad': 'Both bad',
}
CLEARED = '(cleared)'
"""Stands for the network tag of a vote older than the tags' retention."""

Target = Annotated[
    d1.TargetName,
    typer.Argument(help='The vote store: staging, production or private.'),
]
IpTag = Annotated[
    str | None, typer.Option(help='A network tag, as `bench arena votes` lists it.')
]
Actor = Annotated[
    str | None, typer.Option(help='A browser, as `bench arena votes` lists it.')
]
VoteIds = Annotated[
    list[int] | None,
    typer.Option('--vote', help='A vote ID, repeatable.', show_default=False),
]


@app.command()
def prepare(family: FamilyArgument, target: Literal['production', 'private']) -> None:
    """Prepare a cloud publication for review and print its immutable ID."""
    with open_context() as context, d1.connect('private') as database:
        generation = context.state.output_generation(family)
        if generation.id is None:
            raise InputError(f'Family `{family}` has no sealed output collection.')
        metadata = context.state.collection(generation.id)
        if metadata.system_names_json is None:
            raise InputError(
                f'Collection `{generation.id}` has no frozen system names. '
                f'Collect `{family}` again before preparing a publication.'
            )
        try:
            names = TypeAdapter(dict[str, str]).validate_json(
                metadata.system_names_json
            )
        except ValidationError as error:
            raise InputError(error_lines(error, 'Collection system names')) from error
        sources = context.closing_sources(generation.id)
        outputs = read_outputs(family, context.state, generation=generation)
        prepared = publication.prepare(
            sources.family,
            sources.cases,
            outputs,
            names,
            target=target,
        )
        now = datetime.now(UTC)
        publication.stage(database, prepared, now)
        previous = publication.preparation(database, prepared.id)
        if previous is not None:
            original = context.state.collection_generation(previous.collection_id)
            original_outputs = read_outputs(family, context.state, generation=original)
            if _published_outputs(prepared, outputs) != _published_outputs(
                prepared, original_outputs
            ):
                raise InputError(
                    f'Publication `{prepared.id}` already pins collection '
                    f'`{previous.collection_id}`, whose output facts differ. '
                    'Explicitly regrade or rerun the changed outputs before publishing.'
                )
            typer.echo(
                f'Reused prepared publication `{prepared.id}` with its original '
                f'collection `{previous.collection_id}` and case revision '
                f'`{previous.case_revision}`.'
            )
            return
        publication.record_preparation(
            database,
            publication.Preparation(
                publication_id=prepared.id,
                target=target,
                collection_id=generation.id,
                case_revision=metadata.case_revision,
                prepared_at=now,
            ),
        )
    typer.echo(
        f'Prepared `{prepared.id}` for `{target}` with '
        f'{count_of(len(prepared.cases), "case")} and '
        f'{count_of(len(prepared.outputs), "output")}. '
        f'Review it with `bench arena inspect {prepared.id}`.'
    )


def _published_outputs(
    prepared: publication.Publication, outputs: Sequence[Output]
) -> dict[str, str]:
    keys = {output.key for output in prepared.outputs}
    projected: dict[str, str] = {}
    for output in outputs:
        key = publication.output_key(prepared.family, output)
        if key not in keys:
            continue
        if key in projected:
            raise InputError(
                f'Publication `{prepared.id}` has duplicate output `{key}`.'
            )
        projected[key] = json.dumps(
            output.model_dump(mode='json'), sort_keys=True, separators=(',', ':')
        )
    if projected.keys() != keys:
        raise InputError(
            f'Publication `{prepared.id}` is missing outputs '
            'from its pinned collection.'
        )
    return projected


@app.command()
def publish(
    target: Target,
    identity: Annotated[
        str, typer.Argument(help='A reviewed prepared publication ID.')
    ],
) -> None:
    """Stage a reviewed publication in the vote store and activate it."""
    with d1.connect('private') as database:
        prepared = publication.read_prepared(database, identity)
        expected = 'private' if target == 'private' else 'production'
        if expected != prepared.target:
            raise InputError(
                f'Publication `{identity}` was prepared for `{prepared.target}`.'
            )
        if target == 'private':
            changed = publication.activate(database, identity, datetime.now(UTC))
        else:
            with d1.connect(target) as destination:
                now = datetime.now(UTC)
                publication.stage(destination, prepared, now)
                changed = publication.activate(destination, identity, now)

    if not changed:
        typer.echo(
            f'Publication `{prepared.id}` is already active for `{prepared.family}` '
            f'in `{target}`, so nothing changed.'
        )
        return

    typer.echo(
        f'Published `{prepared.family}` to `{target}`: publication `{prepared.id}` '
        f'with {count_of(len(prepared.cases), "case")} and '
        f'{count_of(len(prepared.outputs), "output")}.'
    )


@app.command()
def inspect(identity: str) -> None:
    """Show a prepared publication and the gates of its pinned collection."""
    with d1.connect('private') as database:
        prepared = publication.read_prepared(database, identity)
        metadata = publication.preparation(database, identity)
        assert metadata is not None
        state = State(database)
        generation = state.collection_generation(metadata.collection_id)
        outputs = read_outputs(prepared.family, state, generation=generation)
        typer.echo(publication.listing(prepared, outputs))


@app.command()
def status(
    target: Target,
    family: OptionalFamilyArgument = None,
) -> None:
    """Show each active family's publication and votes, or only the named family's."""
    with d1.connect(target) as database:
        rows = votes.status(database, family)

    if not rows:
        if family is not None:
            raise InputError(
                f'Family `{family}` has no active publication in `{target}`.'
            )
        typer.echo(f'No family has an active publication in `{target}`.')

    for row in rows:
        latest = 'none' if row.latest_vote_at is None else _time(row.latest_vote_at)
        typer.echo(
            f'Family `{row.family}`: publication `{row.publication_id}` active since '
            f'{_time(row.activated_at)}, with {count_of(row.case_count, "case")} and '
            f'{count_of(row.output_count, "output")}. '
            f'{count_of(row.votes, "counted vote")} from '
            f'{count_of(row.voters, "voter")}, {row.excluded} excluded, '
            f'latest at {latest}.'
        )


@app.command('votes')
def list_votes(
    target: Target,
    family: FamilyArgument,
    by: Annotated[
        votes.Grouping | None,
        typer.Option(
            help='Summarize per network tag, the default, or per browser.',
            show_default=False,
        ),
    ] = None,
    ip_tag: IpTag = None,
    actor: Actor = None,
) -> None:
    """Summarize the family's votes per network tag or browser, or list one's votes.

    Both include excluded votes, and the list shows each one's reason.
    """
    selector = _selector(ip_tag=ip_tag, actor=actor)
    if selector is not None and by is not None:
        raise InputError(
            'Pass `--by` only to summarize, without `--ip-tag` or `--actor`.'
        )

    with d1.connect(target) as database:
        if selector is None:
            text = _summary(database, family, by or 'ip')
        else:
            text = _listing(database, family, selector)

    typer.echo(text or f'No vote of `{family}` in `{target}` matches.')


@app.command()
def exclude(
    target: Target,
    family: FamilyArgument,
    reason: Annotated[
        str, typer.Option(help='Why the votes stop counting, kept with each vote.')
    ],
    ip_tag: IpTag = None,
    actor: Actor = None,
    vote: VoteIds = None,
) -> None:
    """Stop counting the votes of one network tag, one browser, or the given IDs.

    A vote excluded earlier keeps its first reason. The next `bench close` leaves
    excluded votes out of the round.
    """
    selector = _required_selector(ip_tag=ip_tag, actor=actor, vote=vote)
    votes.check_reason(reason)
    with d1.connect(target) as database:
        changed = votes.exclude(database, family, selector, reason)

    typer.echo(f'Excluded {count_of(changed, "vote")} of `{family}` in `{target}`.')


@app.command()
def restore(
    target: Target,
    family: FamilyArgument,
    ip_tag: IpTag = None,
    actor: Actor = None,
    vote: VoteIds = None,
) -> None:
    """Count the excluded votes of one network tag, one browser, or the given IDs."""
    selector = _required_selector(ip_tag=ip_tag, actor=actor, vote=vote)
    with d1.connect(target) as database:
        changed = votes.restore(database, family, selector)

    typer.echo(f'Restored {count_of(changed, "vote")} of `{family}` in `{target}`.')


def _summary(database: Database, family: str, by: votes.Grouping) -> str:
    groups = votes.groups(database, family, by)
    return _table(
        (
            'IP tag' if by == 'ip' else 'Actor',
            'Votes',
            'Actors' if by == 'ip' else 'IP tags',
            *CHOICE_LABELS.values(),
            'Median time',
            'First vote',
            'Last vote',
            'Excluded',
        ),
        [
            (
                group.subject or CLEARED,
                str(group.votes),
                str(group.peers),
                *(f'{group.shares[choice]:.0%}' for choice in CHOICE_LABELS),
                _seconds(group.median_elapsed_ms),
                _time(group.first_at),
                _time(group.last_at),
                str(group.excluded),
            )
            for group in groups
        ],
    )


def _listing(database: Database, family: str, selector: votes.Selector) -> str:
    rows = votes.listed(database, family, selector)
    return _table(
        (
            'Vote',
            'Recorded',
            'Actor',
            'IP tag',
            'A',
            'B',
            'Choice',
            'Time',
            'Exclusion',
        ),
        [
            (
                str(row.id),
                _time(row.recorded_at),
                row.actor,
                row.ip_tag or CLEARED,
                row.a_system,
                row.b_system,
                CHOICE_LABELS[row.choice],
                _seconds(row.elapsed_ms),
                row.exclusion or '',
            )
            for row in rows
        ],
    )


def _selector(
    *, ip_tag: str | None, actor: str | None, vote: list[int] | None = None
) -> votes.Selector | None:
    given: dict[str, votes.Selector] = {}
    if ip_tag is not None:
        given['--ip-tag'] = votes.Selector(column='ip_tag', values=(ip_tag,))
    if actor is not None:
        given['--actor'] = votes.Selector(column='actor_id', values=(actor,))
    if vote:
        given['--vote'] = votes.Selector(column='id', values=tuple(vote))
    if len(given) > 1:
        raise InputError(f'Pass only one of {code(given)}.')

    return next(iter(given.values()), None)


def _required_selector(
    *, ip_tag: str | None, actor: str | None, vote: list[int] | None
) -> votes.Selector:
    selector = _selector(ip_tag=ip_tag, actor=actor, vote=vote)
    if selector is None:
        raise InputError('Pass one of `--ip-tag`, `--actor` or `--vote`.')

    return selector


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Align the rows under the header, or return an empty string without rows."""
    if not rows:
        return ''

    widths = [max(map(len, column)) for column in zip(header, *rows, strict=True)]
    return '\n'.join(
        '  '.join(
            cell.ljust(width) for cell, width in zip(line, widths, strict=True)
        ).rstrip()
        for line in (header, *rows)
    )


def _seconds(milliseconds: float) -> str:
    return f'{milliseconds / 1000:.1f} s'


def _time(milliseconds: int) -> str:
    return f'{datetime.fromtimestamp(milliseconds / 1000, UTC):%Y-%m-%d %H:%M} UTC'
