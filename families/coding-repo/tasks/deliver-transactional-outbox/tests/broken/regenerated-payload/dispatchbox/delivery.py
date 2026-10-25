"""Delivery variant that rebuilds stored notification payloads."""

import json
from pathlib import Path

from dispatchbox.models import DeliveryError, DispatchResult, Transport
from dispatchbox.store import Store


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
                transport.send(
                    notification.key,
                    json.dumps(json.loads(notification.payload)).encode(),
                )
            except DeliveryError:
                failed += 1
                continue

            store.mark_delivered(notification.id)
            delivered += 1

    return DispatchResult(attempted, delivered, failed)
