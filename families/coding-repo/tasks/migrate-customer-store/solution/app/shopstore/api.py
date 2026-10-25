"""The serialized customer API."""

from .repository import Store


def customer_json(store: Store, customer_id: int) -> dict[str, int | str]:
    customer = store.get_customer(customer_id)
    return {'id': customer.id, 'name': customer.display_name}
