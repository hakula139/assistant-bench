"""Command-line access to the order and fake receiver stores."""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from .delivery import dispatch_pending
from .models import DeliveryError, DispatchResult, Order
from .orders import place_order
from .receiver import FakeReceiver


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    place = commands.add_parser('place')
    place.add_argument('--db', type=Path, required=True)
    place.add_argument('--order-id', required=True)
    place.add_argument('--amount-cents', type=int, required=True)
    dispatch = commands.add_parser('dispatch')
    dispatch.add_argument('--db', type=Path, required=True)
    dispatch.add_argument('--receiver', type=Path, required=True)
    dispatch.add_argument('--limit', type=int, default=100)
    dispatch.add_argument('--fail-key', action='append', default=[])
    args = parser.parse_args()

    try:
        result: Order | DispatchResult
        if args.command == 'place':
            result = place_order(args.db, args.order_id, args.amount_cents)
        else:
            receiver = FakeReceiver(args.receiver, fail_keys=frozenset(args.fail_key))
            result = dispatch_pending(args.db, receiver, limit=args.limit)

        print(json.dumps(asdict(result)))
    except (ValueError, DeliveryError, sqlite3.Error, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1

    return 0
