"""Validate the JSON outcomes exchanged with the candidate parser probe."""

import json
from typing import TypedDict, TypeGuard


class Success(TypedDict):
    ok: str


class Failure(TypedDict):
    error: str
    message: str
    attrs: list[object]
    is_value_error: bool


type Outcome = Success | Failure
type Results = dict[str, list[Outcome]]


def _mapping(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _sequence(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _outcome(value: object) -> Outcome:
    if _mapping(value):
        ok = value.get('ok')
        if value.keys() == {'ok'} and isinstance(ok, str):
            return Success(ok=ok)

        error = value.get('error')
        message = value.get('message')
        attrs = value.get('attrs')
        is_value_error = value.get('is_value_error')
        if (
            value.keys() == {'error', 'message', 'attrs', 'is_value_error'}
            and isinstance(error, str)
            and isinstance(message, str)
            and _sequence(attrs)
            and len(attrs) == 3
            and isinstance(is_value_error, bool)
        ):
            return Failure(
                error=error, message=message, attrs=attrs, is_value_error=is_value_error
            )

    raise ValueError('Invalid parser probe outcome.')


def read_results(text: str) -> Results:
    """Read named outcome lists, rejecting malformed candidate output."""
    value: object = json.loads(text)
    if not _mapping(value):
        raise ValueError('Parser probe output must be an object.')

    results: Results = {}
    for name, outcomes in value.items():
        if not isinstance(name, str) or not _sequence(outcomes):
            raise ValueError('Parser probe entries must name outcome lists.')

        results[name] = [_outcome(outcome) for outcome in outcomes]

    return results
