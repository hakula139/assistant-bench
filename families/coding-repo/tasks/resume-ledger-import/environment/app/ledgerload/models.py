"""Import results and record-indexed source failures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportResult:
    processed: int
    """Logical records consumed during this invocation, including duplicates."""

    inserted: int
    """New ledger rows committed during this invocation."""

    checkpoint: int
    """Absolute number of logical data records committed for this source."""


@dataclass(frozen=True)
class LedgerRow:
    external_id: str
    amount_cents: int
    memo: str


class SourceError(ValueError):
    """Invalid source or logical record. Index zero identifies file-level errors."""

    def __init__(self, record_index: int, message: str) -> None:
        self.record_index = record_index
        super().__init__(f'Record {record_index}: {message}')


class ConflictError(SourceError):
    """An external ID already belongs to a different amount or memo."""
