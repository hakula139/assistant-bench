"""Parsers for pipe-delimited export records."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


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


def parse_user(line: str) -> User:
    """Parse ``id|email|active``, where active is ``yes`` or ``no``."""
    parts = [part.strip() for part in line.rstrip("\n").split("|")]
    if len(parts) != 3:
        raise ParseError("user", line, f"expected 3 fields, got {len(parts)}")
    if any(part == "" for part in parts):
        raise ParseError("user", line, "empty field")
    try:
        user_id = int(parts[0])
    except ValueError:
        raise ParseError("user", line, f"id is not an integer: {parts[0]!r}") from None
    email = parts[1]
    if "@" not in email:
        raise ParseError("user", line, f"email has no @: {email!r}")
    if parts[2] not in ("yes", "no"):
        raise ParseError("user", line, f"active must be yes or no: {parts[2]!r}")
    return User(user_id, email, parts[2] == "yes")


def parse_order(line: str) -> Order:
    """Parse ``id|user_id|total``."""
    parts = [part.strip() for part in line.rstrip("\n").split("|")]
    if len(parts) != 3:
        raise ParseError("order", line, f"expected 3 fields, got {len(parts)}")
    if any(part == "" for part in parts):
        raise ParseError("order", line, "empty field")
    try:
        order_id = int(parts[0])
    except ValueError:
        raise ParseError("order", line, f"id is not an integer: {parts[0]!r}") from None
    try:
        user_id = int(parts[1])
    except ValueError:
        raise ParseError("order", line, f"user_id is not an integer: {parts[1]!r}") from None
    try:
        total = Decimal(parts[2])
    except InvalidOperation:
        raise ParseError("order", line, f"total is not a decimal: {parts[2]!r}") from None
    return Order(order_id, user_id, total)


def parse_product(line: str) -> Product:
    """Parse ``sku|name|price|stock``."""
    parts = [part.strip() for part in line.rstrip("\n").split("|")]
    if len(parts) != 4:
        raise ParseError("product", line, f"expected 4 fields, got {len(parts)}")
    if any(part == "" for part in parts):
        raise ParseError("product", line, "empty field")
    sku = parts[0]
    name = parts[1]
    try:
        price = Decimal(parts[2])
    except InvalidOperation:
        raise ParseError("product", line, f"price is not a decimal: {parts[2]!r}") from None
    try:
        stock = int(parts[3])
    except ValueError:
        raise ParseError("product", line, f"stock is not an integer: {parts[3]!r}") from None
    return Product(sku, name, price, stock)
