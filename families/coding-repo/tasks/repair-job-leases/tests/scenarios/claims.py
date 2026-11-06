import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from unittest import mock

from jobq import Queue


STATEMENT_DELAY = 0.05
"""Seconds each SQLite statement waits before it runs in the racing claims.

The wait spaces a claim's statements far wider than the claims' start times
differ, so a claim that reads a job and leases it in separate steps overlaps
another claim's read.
"""

_connect = sqlite3.connect


def slow_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    db = _connect(*args, **kwargs)
    db.set_trace_callback(lambda _: time.sleep(STATEMENT_DELAY))
    return db


def enqueue(path: str, count: int) -> list[int]:
    with Queue(Path(path)) as queue:
        return [queue.enqueue('task', index) for index in range(count)]


def threads(rounds: int, workers: int) -> list[dict[str, Any]]:
    """Race `workers` threads for two fresh jobs in each round."""
    path = Path(tempfile.mkdtemp()) / 'jobs.db'
    queue = Queue(path)
    with mock.patch('sqlite3.connect', slow_connect):
        queues = [Queue(path) for _ in range(workers)]
    claimed: list[int | None] = []
    errors: list[str] = []

    def claim(queue: Queue, worker: str) -> None:
        try:
            lease = queue.claim(worker)
        except BaseException as error:
            errors.append(repr(error))
        else:
            claimed.append(None if lease is None else lease.job_id)

    results: list[dict[str, Any]] = []
    for _ in range(rounds):
        jobs = [queue.enqueue('task', index) for index in range(2)]
        claimed.clear()
        errors.clear()
        racers = [
            threading.Thread(target=claim, args=(queue, f'w{index}'))
            for index, queue in enumerate(queues)
        ]
        for racer in racers:
            racer.start()
        for racer in racers:
            racer.join(30)
        results.append({'jobs': jobs, 'claimed': list(claimed), 'errors': errors[:]})
    return results


def claim_on_request(path: str, worker: str) -> None:
    """Claim once for each line on stdin, printing the leased job's ID."""
    sqlite3.connect = slow_connect
    queue = Queue(Path(path))
    print('ready', flush=True)
    for _ in sys.stdin:
        lease = queue.claim(worker)
        print(json.dumps(None if lease is None else lease.job_id), flush=True)


def drain_on_request(path: str, worker: str) -> None:
    """Claim until the queue is empty once a line arrives, printing the IDs."""
    queue = Queue(Path(path))
    print('ready', flush=True)
    sys.stdin.readline()
    claimed: list[int] = []
    while (lease := queue.claim(worker)) is not None:
        claimed.append(lease.job_id)
    print(json.dumps(claimed), flush=True)


def hold_write_lock(path: str) -> None:
    """Hold the database's write lock from `locked` until a line arrives."""
    db = sqlite3.connect(path, isolation_level=None, timeout=10)
    db.execute('BEGIN IMMEDIATE')
    print('locked', flush=True)
    sys.stdin.readline()
    time.sleep(0.5)
    db.execute('COMMIT')


def distinct_tokens() -> dict[str, Any]:
    with Queue(Path(tempfile.mkdtemp()) / 'jobs.db') as queue:
        for index in range(2):
            queue.enqueue('task', index)
        first = queue.claim('w1')
        second = queue.claim('w2')
        third = queue.claim('w3')
    assert first is not None and second is not None
    return {
        'first': [first.job_id, first.token],
        'second': [second.job_id, second.token],
        'third': None if third is None else third.job_id,
    }
