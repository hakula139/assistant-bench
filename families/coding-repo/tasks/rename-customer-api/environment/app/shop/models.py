"""Domain models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    email: str


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int
    amount_cents: int
