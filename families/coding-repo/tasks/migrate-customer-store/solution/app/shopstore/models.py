"""Customer records and public failures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Customer:
    id: int
    display_name: str


class CustomerNotFoundError(LookupError):
    def __init__(self, customer_id: int) -> None:
        self.customer_id = customer_id
        super().__init__(f'Customer {customer_id} does not exist.')


class MigrationError(ValueError):
    pass


class SchemaVersionError(ValueError):
    def __init__(self, version: int) -> None:
        self.version = version
        super().__init__(f'Schema version {version} is unsupported.')
