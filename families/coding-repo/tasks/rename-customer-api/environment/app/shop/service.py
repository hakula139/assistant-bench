"""Customer-facing operations."""

from shop.repository import get_customer, orders_for


def greeting(customer_id: int) -> str:
    customer = get_customer(customer_id)
    first = customer.name.split()[0]
    return f"Welcome back, {first}!"


def lifetime_value_cents(customer_id: int) -> int:
    get_customer(customer_id)
    return sum(order.amount_cents for order in orders_for(customer_id))
