"""The style of the messages an author reads: code spans and validation errors."""

import tomllib
from collections.abc import Iterable

from pydantic import TypeAdapter, ValidationError


class InputError(ValueError):
    """A refusal of an author's or operator's input, printed without a traceback."""


def code(values: Iterable[object]) -> str:
    """Return the values as comma-separated code spans."""
    return ', '.join(f'`{value}`' for value in values)


def series(items: Iterable[str]) -> str:
    """Return the items joined with commas and a final `and`."""
    *rest, last = items
    return f'{", ".join(rest)} and {last}' if rest else last


def count_of(value: int, noun: str) -> str:
    return f'{value} {noun}' if value == 1 else f'{value} {noun}s'


def sentence(text: str) -> str:
    return text if text.endswith('.') else f'{text}.'


def error_lines(error: ValidationError, prefix: str, *, skip: int = 0) -> str:
    """Return one sentence per validation error, starting with `prefix` and the field.

    `skip` drops that many leading parts of each error's path.
    """
    lines: list[str] = []
    for detail in error.errors():
        match detail['type']:
            case 'value_error':
                context = detail.get('ctx')
                assert context is not None
                message = str(context['error'])
            case 'model_type':
                # Pydantic's message names the model class, which an author never sees.
                message = 'Input should be a valid dictionary'
            case _:
                message = detail['msg']

        path = '.'.join(str(part) for part in detail['loc'][skip:])
        line = f'{prefix}: `{path}`: {message}' if path else f'{prefix}: {message}'
        lines.append(sentence(line))

    return '\n'.join(lines)


def validated_toml[T](
    adapter: TypeAdapter[T], text: str, prefix: str, *, skip: int = 0
) -> T:
    """Parse and validate a TOML document, wording its errors with `error_lines`.

    Raises
    ------
    InputError
        The text is not valid TOML or does not validate.
    """
    try:
        return adapter.validate_python(tomllib.loads(text))
    except tomllib.TOMLDecodeError as error:
        raise InputError(sentence(f'{prefix} is not valid TOML: {error}')) from error
    except ValidationError as error:
        raise InputError(error_lines(error, prefix, skip=skip)) from error
