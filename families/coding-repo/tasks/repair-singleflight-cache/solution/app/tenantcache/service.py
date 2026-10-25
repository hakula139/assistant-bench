"""Cached account lookup through the repository."""

from .cache import SingleFlightCache
from .models import Account
from .repository import AccountRepository


class AccountService:
    def __init__(
        self, repository: AccountRepository, cache: SingleFlightCache[Account]
    ) -> None:
        self.repository = repository
        self.cache = cache

    async def get_account(self, tenant: str, account_id: str) -> Account:
        return await self.cache.get(
            tenant, account_id, lambda: self.repository.load_account(tenant, account_id)
        )
