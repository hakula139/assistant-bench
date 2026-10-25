"""Public order and delivery contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Order:
    order_id: str
    amount_cents: int


@dataclass(frozen=True)
class Notification:
    id: int
    order_id: str
    key: str
    payload: bytes


@dataclass(frozen=True)
class DispatchResult:
    attempted: int
    delivered: int
    failed: int


class ConflictError(ValueError):
    pass


class DeliveryError(Exception):
    pass


class Transport(Protocol):
    def send(self, key: str, payload: bytes) -> None:
        """Accept a deduplicated effect or raise DeliveryError."""
        ...
