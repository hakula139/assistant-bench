"""Deliver pending records without hiding database failures."""

from pathlib import Path

from .models import DeliveryError, DispatchResult, Transport
from .store import Store


def dispatch_pending(
    db: Path, transport: Transport, *, limit: int = 100
) -> DispatchResult:
    if type(limit) is not int or limit <= 0:
        raise ValueError('Limit must be a positive integer.')

    attempted = delivered = failed = 0

    with Store(db) as store:
        for notification in store.pending(limit):
            attempted += 1

            try:
                transport.send(notification.key, notification.payload)
            except DeliveryError:
                failed += 1
                continue

            store.mark_delivered(notification.id)
            delivered += 1

    return DispatchResult(attempted, delivered, failed)
