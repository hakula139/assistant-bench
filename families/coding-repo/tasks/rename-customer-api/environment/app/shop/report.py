"""Plain-text reports."""

from collections.abc import Iterable

from shop.repository import get_customer, orders_for
from shop.service import lifetime_value_cents


def customer_line(customer_id: int) -> str:
    customer = get_customer(customer_id)
    count = len(orders_for(customer_id))
    value = lifetime_value_cents(customer_id) / 100
    return f"#{customer.id} {customer.name} <{customer.email}>: {count} orders, ${value:.2f}"


def customer_report(customer_ids: Iterable[int]) -> str:
    lines = [customer_line(customer_id) for customer_id in customer_ids]
    return "\n".join(sorted(lines, key=lambda line: line.split(" ", 1)[1]))
