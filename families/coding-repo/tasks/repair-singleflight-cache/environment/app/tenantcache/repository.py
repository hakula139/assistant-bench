"""The asynchronous account repository contract."""

from typing import Protocol

from .models import Account


class AccountRepository(Protocol):
    async def load_account(self, tenant: str, account_id: str) -> Account:
        """Return an account or propagate the repository's lookup failure."""
        ...
