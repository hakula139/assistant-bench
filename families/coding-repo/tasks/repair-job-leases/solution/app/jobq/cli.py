"""Command-line entry point."""

import argparse
import dataclasses
import importlib
import json
import logging
from pathlib import Path

from .errors import UnknownJob
from .queue import Queue
from .worker import Worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='jobq')
    parser.add_argument('database', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)

    enqueue = commands.add_parser('enqueue', help='add a job')
    enqueue.add_argument('kind')
    enqueue.add_argument('payload', nargs='?', default='null', help='JSON payload')
    enqueue.add_argument('--dedupe-key')
    enqueue.add_argument('--delay', type=float, default=0.0)

    status = commands.add_parser('status', help='show one job')
    status.add_argument('job_id', type=int)

    commands.add_parser('counts', help='count jobs by status')
    commands.add_parser('dead', help='list dead-lettered jobs')

    work = commands.add_parser('work', help='run jobs with the handlers of a module')
    work.add_argument('handlers', help='module whose HANDLERS maps kinds to callables')
    work.add_argument('--name', required=True)
    work.add_argument('--lease-seconds', type=float, default=30.0)
    work.add_argument('--until-idle', action='store_true')

    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.payload) if args.command == 'enqueue' else None
    except json.JSONDecodeError as error:
        parser.error(f'invalid payload: {error}')

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    with Queue(args.database) as queue:
        match args.command:
            case 'enqueue':
                job_id = queue.enqueue(
                    args.kind, payload, dedupe_key=args.dedupe_key, delay=args.delay
                )
                print(job_id)
            case 'status':
                try:
                    _print(dataclasses.asdict(queue.get(args.job_id)))
                except UnknownJob as error:
                    parser.exit(1, f'jobq: {error}\n')
            case 'counts':
                _print(queue.counts())
            case 'dead':
                _print([dataclasses.asdict(job) for job in queue.dead_letters()])
            case 'work':
                handlers = importlib.import_module(args.handlers).HANDLERS
                worker = Worker(
                    queue, handlers, name=args.name, lease_seconds=args.lease_seconds
                )
                print(worker.run(until_idle=args.until_idle))
            case _:
                raise AssertionError(args.command)
    return 0


def _print(value: object) -> None:
    print(json.dumps(value, sort_keys=True))
