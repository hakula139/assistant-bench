"""Print, as JSON, how the record parsers behave on every probe input."""

import json
import sys
from collections.abc import Callable

from probe_contract import Failure, Outcome, Success
from records import parsers


INPUTS = {
    'parse_user': [
        '7|ada@example.com|yes',
        '7|ada@example.com|yes\n',
        ' 8 | bob@example.com | no ',
        '9|carol@example.com|Yes',
        '9|carol@example.com|true',
        'x|a@b|yes',
        '1_000|a@b|no',
        '-3|a@b|no',
        '+4|a@b|yes',
        '٣|a@b|yes',
        '5|no-at-sign|yes',
        '5||yes',
        '5|a@b|',
        '5|a@b',
        '5|a@b|yes|extra',
        '',
        '|',
        '5|a@b|yes\r\n',
        '5|a@b|yes\n\n',
        '  |a@b|yes',
        '5 | a@b@c | no',
        '1e3|a@b|yes',
        '007|a@b|yes',
    ],
    'parse_order': [
        '3|7|19.90',
        ' 3 | 7 | 19.90 ',
        '3|7|1e2',
        '3|7|-0.00',
        '3|7|NaN',
        '3|7|Infinity',
        '3|7|abc',
        '3|7|1,000.00',
        '3|x|1.00',
        'x|7|1.00',
        '3.5|7|1.00',
        '3|7',
        '3|7|1|2',
        '3||1.00',
        '3|7|',
        '1_0|2_0|3_0',
        '3|7|.5',
        '3|7| 5. ',
        '3|7|19.90\n',
    ],
    'parse_product': [
        'AB-1|Widget|2.50|12',
        ' AB-1 | Big Widget | 2.5 | 0 ',
        'AB-1|Widget|2.50|-1',
        'AB-1|Widget|free|12',
        'AB-1|Widget|2.50|many',
        'AB-1|Widget|2.50|1.0',
        'AB-1|Widget|2.50',
        'AB-1|Widget|2.50|12|x',
        '|Widget|2.50|12',
        'AB-1||2.50|12',
        'AB-1|Widget||12',
        'AB-1|Widget|2.50|',
        'AB-1|Widget|1E-3|1_2',
        'AB-1|Wid|get|2.50|12',
        'AB-1|Widget|sNaN|1',
        'AB-1|Widget|2.50|12\n',
    ],
}


def describe(fn: Callable[[str], object], line: str) -> Outcome:
    try:
        return Success(ok=repr(fn(line)))
    # Every outcome is recorded, including unexpected exception types.
    except Exception as error:
        return Failure(
            error=type(error).__name__,
            message=str(error),
            attrs=[getattr(error, name, None) for name in ('kind', 'line', 'reason')],
            is_value_error=isinstance(error, ValueError),
        )


PARSERS: dict[str, Callable[[str], object]] = {
    'parse_user': parsers.parse_user,
    'parse_order': parsers.parse_order,
    'parse_product': parsers.parse_product,
}

json.dump(
    {
        name: [describe(PARSERS[name], line) for line in lines]
        for name, lines in INPUTS.items()
    },
    sys.stdout,
    indent=1,
    sort_keys=True,
)
