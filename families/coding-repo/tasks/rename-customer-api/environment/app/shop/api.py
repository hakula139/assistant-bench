"""JSON-ready handlers."""

from shop.repository import get_customer
from shop.service import lifetime_value_cents


def handle_get_customer(customer_id: int) -> dict[str, object]:
    customer = get_customer(customer_id)
    return {"id": customer.id, "name": customer.name, "email": customer.email}


def handle_customer_summary(customer_id: int) -> dict[str, object]:
    customer = get_customer(customer_id)
    return {"name": customer.name, "lifetime_value_cents": lifetime_value_cents(customer_id)}
