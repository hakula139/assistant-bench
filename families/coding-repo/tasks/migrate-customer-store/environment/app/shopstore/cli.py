"""Read customer reports from persisted stores."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .migrations import open_store
from .report import customer_report


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    report = commands.add_parser('report')
    report.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()

    try:
        with open_store(args.db) as store:
            print(json.dumps(customer_report(store)))
    except (ValueError, sqlite3.Error, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1

    return 0
