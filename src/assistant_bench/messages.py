"""The style of the messages an author reads: code spans and validation errors."""

from collections.abc import Iterable

from pydantic import ValidationError


def code(values: Iterable[object]) -> str:
    """Return the values as comma-separated code spans."""
    return ', '.join(f'`{value}`' for value in values)


def count_of(value: int, noun: str) -> str:
    """Return the count with its noun, adding an `s` unless the count is 1."""
    return f'{value} {noun}' if value == 1 else f'{value} {noun}s'


def sentence(text: str) -> str:
    return text if text.endswith('.') else f'{text}.'


def error_lines(error: ValidationError, prefix: str, *, skip: int = 0) -> str:
    """Return one sentence per validation error, starting with `prefix` and the field.

    `skip` drops that many leading parts of each error's path, such as the tag
    of a tagged union.
    """
    lines: list[str] = []
    for detail in error.errors():
        if detail['type'] == 'value_error':
            context = detail.get('ctx')
            assert context is not None
            message = str(context['error'])
        else:
            message = detail['msg']

        path = '.'.join(str(part) for part in detail['loc'][skip:])
        line = f'{prefix}: `{path}`: {message}' if path else f'{prefix}: {message}'
        lines.append(sentence(line))

    return '\n'.join(lines)
