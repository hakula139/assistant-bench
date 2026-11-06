"""The family arguments and the name refusal the `bench` commands share."""

from collections.abc import Collection, Iterable
from typing import Annotated

import typer

from assistant_bench.families import family_names
from assistant_bench.messages import code


FAMILY_HELP = 'A directory name under families/, such as writing-zh.'


def known_names(
    names: Iterable[str],
    known: Collection[str],
    refusal: str,
    hint: str | None = None,
    *,
    param_hint: str | None = None,
) -> list[str]:
    """Return the names once each, in the order given.

    Raises
    ------
    typer.BadParameter
        A name is missing from `known`. The message is `refusal` followed by the
        missing names and `hint`, which defaults to listing `known`.
    """
    wanted = list(dict.fromkeys(names))
    unknown = [name for name in wanted if name not in known]
    if unknown:
        hint = hint or f'Choose among {code(known)}.'
        raise typer.BadParameter(
            f'{refusal} {code(unknown)}. {hint}', param_hint=param_hint
        )
    return wanted


def _family(name: str | None) -> str | None:
    if name is not None:
        known_names([name], family_names(), 'No family named')
    return name


def _families(names: list[str] | None) -> list[str] | None:
    if names is None:
        return None
    return known_names(names, family_names(), 'No family named')


FamilyArgument = Annotated[
    str, typer.Argument(metavar='FAMILY', help=FAMILY_HELP, callback=_family)
]
OptionalFamilyArgument = Annotated[
    str | None,
    typer.Argument(metavar='[FAMILY]', help=FAMILY_HELP, callback=_family),
]
FamilyArguments = Annotated[
    list[str] | None,
    typer.Argument(metavar='FAMILY...', help=FAMILY_HELP, callback=_families),
]
SystemOptions = Annotated[
    list[str] | None,
    typer.Option(
        '--system',
        help='Limit the run to this system. Without a family, this sends it to '
        'every family that lists it. Repeat for several.',
    ),
]
