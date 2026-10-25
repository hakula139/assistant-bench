"""Order totals in the existing serialized customer shape."""

from typing import TypedDict

from .repository import Store


class CustomerSummary(TypedDict):
    id: int
    name: str
    orders: int
    total_cents: int


class CustomerReport(TypedDict):
    customers: list[CustomerSummary]
    total_cents: int


def customer_report(store: Store) -> CustomerReport:
    totals: dict[int, tuple[int, int]] = {}

    for customer_id, cents in store.connection.execute(
        'SELECT customer_id, amount_cents FROM orders'
    ):
        count, total = totals.get(customer_id, (0, 0))
        totals[customer_id] = count + 1, total + cents

    customers: list[CustomerSummary] = []

    for customer in store.customers():
        count, total = totals.get(customer.id, (0, 0))
        customers.append(
            {
                'id': customer.id,
                'name': customer.full_name,
                'orders': count,
                'total_cents': total,
            }
        )

    return {
        'customers': customers,
        'total_cents': sum(item['total_cents'] for item in customers),
    }
