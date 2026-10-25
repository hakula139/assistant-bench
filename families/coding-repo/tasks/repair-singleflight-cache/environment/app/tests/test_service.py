import unittest

from tenantcache import Account, AccountService, SingleFlightCache


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_account_id_is_scoped_to_its_tenant(self) -> None:
        class Repository:
            async def load_account(self, tenant: str, account_id: str) -> Account:
                return Account(tenant, account_id, tenant)

        service = AccountService(
            Repository(), SingleFlightCache[Account](10, clock=lambda: 0)
        )

        first = await service.get_account('first', 'shared')
        second = await service.get_account('second', 'shared')

        self.assertEqual(first, Account('first', 'shared', 'first'))
        self.assertEqual(second, Account('second', 'shared', 'second'))
