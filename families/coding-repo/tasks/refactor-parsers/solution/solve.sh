#!/usr/bin/env bash

set -euo pipefail

cat >/app/records/parsers.py <<'PY'
"""Parsers for pipe-delimited export records."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class ParseError(ValueError):
    """A record line that does not match its schema."""

    def __init__(self, kind: str, line: str, reason: str) -> None:
        super().__init__(f"invalid {kind} record {line!r}: {reason}")
        self.kind = kind
        self.line = line
        self.reason = reason


@dataclass(frozen=True)
class User:
    id: int
    email: str
    active: bool


@dataclass(frozen=True)
class Order:
    id: int
    user_id: int
    total: Decimal


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal
    stock: int


_CONVERTERS: dict[type, tuple[Callable[[str], Any], type[Exception], str]] = {
    str: (str, ValueError, "a string"),
    int: (int, ValueError, "an integer"),
    Decimal: (Decimal, InvalidOperation, "a decimal"),
}


def _parse_fields(kind: str, line: str, fields: list[tuple[str, type]]) -> list[Any]:
    """Split ``line`` and convert each field, raising ``ParseError`` as the parsers always have."""
    parts = [part.strip() for part in line.rstrip("\n").split("|")]
    if len(parts) != len(fields):
        raise ParseError(kind, line, f"expected {len(fields)} fields, got {len(parts)}")
    if any(part == "" for part in parts):
        raise ParseError(kind, line, "empty field")
    values = []
    for part, (name, target) in zip(parts, fields):
        convert, error, label = _CONVERTERS[target]
        try:
            values.append(convert(part))
        except error:
            raise ParseError(kind, line, f"{name} is not {label}: {part!r}") from None
    return values


def parse_user(line: str) -> User:
    """Parse ``id|email|active``, where active is ``yes`` or ``no``."""
    user_id, email, active = _parse_fields("user", line, [("id", int), ("email", str), ("active", str)])
    if "@" not in email:
        raise ParseError("user", line, f"email has no @: {email!r}")
    if active not in ("yes", "no"):
        raise ParseError("user", line, f"active must be yes or no: {active!r}")
    return User(user_id, email, active == "yes")


def parse_order(line: str) -> Order:
    """Parse ``id|user_id|total``."""
    return Order(*_parse_fields("order", line, [("id", int), ("user_id", int), ("total", Decimal)]))


def parse_product(line: str) -> Product:
    """Parse ``sku|name|price|stock``."""
    return Product(*_parse_fields("product", line, [("sku", str), ("name", str), ("price", Decimal), ("stock", int)]))
PY
