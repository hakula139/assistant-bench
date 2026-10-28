import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from jobq import Lease, Queue


ROUNDS = 5
"""Races per test, each over two fresh jobs."""

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


SLOW_SQLITE = f"""
import sqlite3
import time

_connect = sqlite3.connect


def _slow_connect(*args, **kwargs):
    db = _connect(*args, **kwargs)
    db.set_trace_callback(lambda _: time.sleep({STATEMENT_DELAY}))
    return db


sqlite3.connect = _slow_connect
"""

CLAIM = (
    SLOW_SQLITE
    + """
import json
import sys
from pathlib import Path

from jobq import Queue

queue = Queue(Path(sys.argv[1]))
print('ready', flush=True)
for _ in sys.stdin:
    lease = queue.claim(sys.argv[2])
    print(json.dumps(None if lease is None else lease.job_id), flush=True)
"""
)

DRAIN = """
import json
import sys
from pathlib import Path

from jobq import Queue

queue = Queue(Path(sys.argv[1]))
print('ready', flush=True)
sys.stdin.readline()
claimed = []
while (lease := queue.claim(sys.argv[2])) is not None:
    claimed.append(lease.job_id)
print(json.dumps(claimed), flush=True)
"""


class ConcurrentClaimTest(unittest.TestCase):
    """Claims start together and race, or queue up behind a write lock."""

    def setUp(self) -> None:
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'jobs.db'
        self.queue = self.enterContext(Queue(self.path))
        self.blocker = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.addCleanup(self.blocker.close)

    def enqueue(self, count: int) -> set[int]:
        return {self.queue.enqueue('task', index) for index in range(count)}

    def hold_write_lock(self) -> None:
        self.blocker.execute('BEGIN IMMEDIATE')

    def release_write_lock(self) -> None:
        time.sleep(0.5)
        self.blocker.execute('COMMIT')

    def assert_distinct_leases(
        self, claimed: list[int | None], jobs: set[int], attempt: int = 0
    ) -> None:
        leased = [job_id for job_id in claimed if job_id is not None]
        self.assertEqual(sorted(leased), sorted(jobs), f'round {attempt}: {claimed}')

    def test_threads(self) -> None:
        with mock.patch('sqlite3.connect', slow_connect):
            queues = [self.enterContext(Queue(self.path)) for _ in range(6)]
        claimed: list[int | None] = []
        errors: list[BaseException] = []

        def claim(queue: Queue, worker: str) -> None:
            try:
                lease = queue.claim(worker)
            except BaseException as error:
                errors.append(error)
            else:
                claimed.append(None if lease is None else lease.job_id)

        for attempt in range(ROUNDS):
            jobs = self.enqueue(2)
            claimed.clear()
            threads = [
                threading.Thread(target=claim, args=(queue, f'w{index}'))
                for index, queue in enumerate(queues)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)

            self.assertEqual(errors, [], f'round {attempt}')
            self.assert_distinct_leases(claimed, jobs, attempt)

    def start(self, script: str, count: int) -> list[subprocess.Popen[str]]:
        """Start `count` processes running `script` and wait until each is ready."""
        workers = [
            subprocess.Popen(
                [sys.executable, '-c', script, str(self.path), f'p{index}'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            for index in range(count)
        ]
        for worker in workers:
            self.addCleanup(worker.kill)
            assert worker.stdout is not None
            self.assertEqual(worker.stdout.readline(), 'ready\n')
        return workers

    def release_workers(
        self, workers: list[subprocess.Popen[str]], *, locked: bool
    ) -> list[Any]:
        """Let each worker run one step and read its result.

        When `locked`, the step waits behind the write lock until every worker
        has started it.
        """
        for worker in workers:
            assert worker.stdin is not None
            worker.stdin.write('go\n')
            worker.stdin.flush()
        if locked:
            self.release_write_lock()

        results: list[Any] = []
        for worker in workers:
            assert worker.stdout is not None
            results.append(json.loads(worker.stdout.readline()))
        return results

    def finish(self, workers: list[subprocess.Popen[str]]) -> None:
        for worker in workers:
            worker.communicate(timeout=60)
        self.assertEqual([worker.returncode for worker in workers], [0] * len(workers))

    def test_processes(self) -> None:
        workers = self.start(CLAIM, 4)

        for attempt in range(ROUNDS):
            jobs = self.enqueue(2)
            claimed = self.release_workers(workers, locked=False)

            self.assert_distinct_leases(claimed, jobs, attempt)
        self.finish(workers)

    def test_processes_draining_the_queue(self) -> None:
        jobs = self.enqueue(100)
        workers = self.start(DRAIN, 4)
        self.hold_write_lock()

        claimed = [
            job_id
            for result in self.release_workers(workers, locked=True)
            for job_id in result
        ]

        self.finish(workers)
        self.assert_distinct_leases(claimed, jobs)

    def test_tokens_identify_one_lease(self) -> None:
        self.enqueue(2)

        first = self.queue.claim('w1')
        second = self.queue.claim('w2')

        assert isinstance(first, Lease) and isinstance(second, Lease)
        self.assertNotEqual(first.token, second.token)
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertIsNone(self.queue.claim('w3'))
