"""Logical CSV records with exact signed 64-bit cents."""

import csv
import io
import re
from collections.abc import Iterator

from .models import ImportError, LedgerRow


_AMOUNT = re.compile(r'-?[0-9]+\.[0-9]{2}')


def records(content: bytes) -> Iterator[tuple[int, LedgerRow]]:
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ImportError(0, 'CSV must use UTF-8.') from exc

    reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
    try:
        headers = reader.fieldnames
    except csv.Error as exc:
        raise ImportError(0, 'Malformed CSV header.') from exc

    if (
        headers is None
        or len(headers) != 3
        or set(headers) != {'external_id', 'amount', 'memo'}
    ):
        raise ImportError(0, 'Headers must be external_id, amount and memo.')

    index = 0
    try:
        for index, row in enumerate(reader, 1):
            identifier = row.get('external_id')
            amount = row.get('amount')
            memo = row.get('memo')
            if (
                None in row
                or not identifier
                or amount is None
                or memo is None
                or _AMOUNT.fullmatch(amount) is None
            ):
                raise ImportError(index, 'Invalid ID, amount or column count.')

            sign = -1 if amount.startswith('-') else 1
            whole, fraction = amount.lstrip('-').split('.')
            digits = whole.lstrip('0') or '0'
            if len(digits) > 17:
                raise ImportError(index, 'Amount exceeds signed 64-bit cents.')

            cents = sign * (int(digits) * 100 + int(fraction))
            if not -(2**63) <= cents < 2**63:
                raise ImportError(index, 'Amount exceeds signed 64-bit cents.')

            yield index, LedgerRow(identifier, cents, memo)
    except csv.Error as exc:
        raise ImportError(index + 1, 'Malformed CSV record.') from exc
