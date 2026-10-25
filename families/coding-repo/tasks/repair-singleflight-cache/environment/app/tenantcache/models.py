"""Account identity and display data."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    tenant: str
    id: str
    name: str
