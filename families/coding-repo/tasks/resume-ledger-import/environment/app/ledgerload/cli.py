"""JSON commands for importing and reporting persistent ledger rows."""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from .importer import import_file
from .store import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    importer = commands.add_parser('import')
    importer.add_argument('--db', type=Path, required=True)
    importer.add_argument('--file', type=Path, required=True)
    importer.add_argument('--source-id', required=True)
    importer.add_argument('--batch-size', type=int, default=100)
    report = commands.add_parser('report')
    report.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == 'import':
            result = asdict(
                import_file(
                    args.db,
                    args.file,
                    source_id=args.source_id,
                    batch_size=args.batch_size,
                )
            )
        else:
            with Store(args.db) as store:
                result = store.report()
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0
