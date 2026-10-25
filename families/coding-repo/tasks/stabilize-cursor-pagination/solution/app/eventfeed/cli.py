"""JSON commands for appending and paging events."""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from .models import Event
from .service import page
from .store import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    append = commands.add_parser('append')
    append.add_argument('--db', type=Path, required=True)
    append.add_argument('--id', required=True)
    append.add_argument('--occurred-at', required=True, type=int)
    append.add_argument('--tag', default='')
    listing = commands.add_parser('page')
    listing.add_argument('--db', type=Path, required=True)
    listing.add_argument('--limit', type=int, default=20)
    listing.add_argument('--tag')
    listing.add_argument('--cursor')
    args = parser.parse_args()

    try:
        if args.command == 'append':
            event = Event(args.id, args.occurred_at, args.tag)
            with Store(args.db) as store:
                store.append(event)

            result = asdict(event)
        else:
            result = asdict(
                page(args.db, limit=args.limit, tag=args.tag, cursor=args.cursor)
            )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0
