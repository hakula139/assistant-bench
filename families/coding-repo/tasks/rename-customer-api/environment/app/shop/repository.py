"""In-memory storage."""

from shop.models import Customer, Order

_CUSTOMERS: dict[int, Customer] = {
    1: Customer(1, "Ada Lovelace", "ada@example.com"),
    2: Customer(2, "Grace Hopper", "grace@example.com"),
    3: Customer(3, "Alan Turing", "alan@example.com"),
}

_ORDERS: list[Order] = [
    Order(10, 1, 1999),
    Order(11, 1, 500),
    Order(12, 3, 12000),
]


def get_customer(customer_id: int) -> Customer:
    """Return the customer with ``customer_id``, or raise ``LookupError``."""
    try:
        return _CUSTOMERS[customer_id]
    except KeyError:
        raise LookupError(f"no customer {customer_id}") from None


def orders_for(customer_id: int) -> list[Order]:
    """Return the customer's orders, oldest first."""
    return [order for order in _ORDERS if order.customer_id == customer_id]
