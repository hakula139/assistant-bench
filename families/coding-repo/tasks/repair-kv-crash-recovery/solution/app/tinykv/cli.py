"""Command-line access to a store, with UTF-8 keys and values."""

import argparse
import sys
from pathlib import Path

from .errors import StoreError
from .store import DEFAULT_SEGMENT_BYTES, Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='tinykv')
    parser.add_argument('directory', type=Path)
    parser.add_argument('--segment-bytes', type=int, default=DEFAULT_SEGMENT_BYTES)
    commands = parser.add_subparsers(dest='command', required=True)
    put = commands.add_parser('put')
    put.add_argument('key')
    put.add_argument('value')
    get = commands.add_parser('get')
    get.add_argument('key')
    delete = commands.add_parser('delete')
    delete.add_argument('key')
    scan = commands.add_parser('scan')
    scan.add_argument('prefix', nargs='?', default='')
    commands.add_parser('compact')
    args = parser.parse_args(argv)

    try:
        with Store.open(args.directory, segment_bytes=args.segment_bytes) as store:
            return _run(store, args)
    except (StoreError, ValueError, OSError) as error:
        print(f'tinykv: {error}', file=sys.stderr)
        return 1


def _run(store: Store, args: argparse.Namespace) -> int:
    match args.command:
        case 'put':
            store.put(args.key.encode(), args.value.encode())
        case 'get':
            value = store.get(args.key.encode())
            if value is None:
                print(f'tinykv: {args.key} not found', file=sys.stderr)
                return 1
            print(value.decode(errors='replace'))
        case 'delete':
            if not store.delete(args.key.encode()):
                print(f'tinykv: {args.key} not found', file=sys.stderr)
                return 1
        case 'scan':
            for key, value in store.items(args.prefix.encode()):
                print(
                    f'{key.decode(errors="replace")}\t{value.decode(errors="replace")}'
                )
        case _:
            store.compact()

    return 0
