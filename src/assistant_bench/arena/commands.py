"""Publication, inspection and moderation of the arena's vote stores."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from assistant_bench import jobs
from assistant_bench.arena import publication, votes
from assistant_bench.arena.d1 import D1Error, Database, Target, connect
from assistant_bench.catalog import load_export, load_systems
from assistant_bench.families import Choice, family_names, load_cases, load_family
from assistant_bench.messages import code, count_of


app = typer.Typer(no_args_is_help=True)


def _known_family(name: str | None) -> str | None:
    """Return the name, refusing one with no family."""
    known = family_names()
    if name is not None and name not in known:
        raise typer.BadParameter(
            f'No family named `{name}`. Choose among {code(known)}.'
        )
    return name


FAMILY_HELP = 'A directory name under families/, such as writing-zh.'

CHOICE_LABELS: dict[Choice, str] = {
    'a': 'A',
    'b': 'B',
    'tie': 'Tie',
    'both_bad': 'Both bad',
}
CLEARED = '(cleared)'
"""Stands for the network tag of a vote older than the tags' retention."""

FamilyArgument = Annotated[
    str, typer.Argument(metavar='FAMILY', help=FAMILY_HELP, callback=_known_family)
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
def prepare(family: FamilyArgument) -> None:
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
        f'Publication of `{family}` with {count_of(len(prepared.cases), "case")} '
        f'and {count_of(len(prepared.outputs), "output")} written to `{path}`. Review '
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
        f'with {count_of(len(prepared.cases), "case")} and '
        f'{count_of(len(prepared.outputs), "output")}.'
    )


@app.command()
def status(
    target: Target,
    family: Annotated[
        str | None,
        typer.Argument(metavar='[FAMILY]', help=FAMILY_HELP, callback=_known_family),
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
        _fail('Pass `--by` only to summarize, without `--ip-tag` or `--actor`.')

    try:
        with connect(target) as database:
            if selector is None:
                text = _summary(database, family, by or 'ip')
            else:
                text = _listing(database, family, selector)
    except (D1Error, OSError, ValueError) as error:
        _fail(str(error))

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
    try:
        votes.check_reason(reason)
        with connect(target) as database:
            changed = votes.exclude(database, family, selector, reason)
    except (D1Error, OSError, ValueError) as error:
        _fail(str(error))

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
    try:
        with connect(target) as database:
            changed = votes.restore(database, family, selector)
    except (D1Error, OSError, ValueError) as error:
        _fail(str(error))

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
    """Return the selector the options give, or None when they give none."""
    given: dict[str, votes.Selector] = {}
    if ip_tag is not None:
        given['--ip-tag'] = votes.Selector(column='ip_tag', values=(ip_tag,))
    if actor is not None:
        given['--actor'] = votes.Selector(column='actor_id', values=(actor,))
    if vote:
        given['--vote'] = votes.Selector(column='id', values=tuple(vote))
    if len(given) > 1:
        _fail(f'Pass only one of {code(given)}.')

    return next(iter(given.values()), None)


def _required_selector(
    *, ip_tag: str | None, actor: str | None, vote: list[int] | None
) -> votes.Selector:
    selector = _selector(ip_tag=ip_tag, actor=actor, vote=vote)
    if selector is None:
        _fail('Pass one of `--ip-tag`, `--actor` or `--vote`.')

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


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)
