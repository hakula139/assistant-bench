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

from jobq import Lease, Queue


CLAIM = """
import json
import sys
from pathlib import Path

from jobq import Queue

queue = Queue(Path(sys.argv[1]))
print('ready', flush=True)
sys.stdin.readline()
lease = queue.claim(sys.argv[2])
print(json.dumps(None if lease is None else lease.job_id), flush=True)
"""

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
    """Claims queue up behind another connection's write lock, then race."""

    def setUp(self) -> None:
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'jobs.db'
        self.queue = self.enterContext(Queue(self.path))
        self.jobs = {self.queue.enqueue('task', index) for index in range(2)}

        self.blocker = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.addCleanup(self.blocker.close)

    def hold_write_lock(self) -> None:
        self.blocker.execute('BEGIN IMMEDIATE')

    def release_write_lock(self) -> None:
        time.sleep(0.5)
        self.blocker.execute('COMMIT')

    def assert_distinct_leases(self, claimed: list[int | None]) -> None:
        leased = [job_id for job_id in claimed if job_id is not None]
        self.assertEqual(sorted(leased), sorted(self.jobs), claimed)

    def test_threads(self) -> None:
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

        threads = [
            threading.Thread(target=claim, args=(queue, f'w{index}'))
            for index, queue in enumerate(queues)
        ]
        self.hold_write_lock()
        for thread in threads:
            thread.start()
        self.release_write_lock()
        for thread in threads:
            thread.join(30)

        self.assertEqual(errors, [])
        self.assert_distinct_leases(claimed)

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

    def results(self, workers: list[subprocess.Popen[str]]) -> list[Any]:
        for worker in workers:
            assert worker.stdin is not None
            worker.stdin.write('go\n')
            worker.stdin.flush()
        self.release_write_lock()
        results = [json.loads(worker.communicate(timeout=60)[0]) for worker in workers]
        self.assertEqual([worker.returncode for worker in workers], [0] * len(workers))
        return results

    def test_processes(self) -> None:
        workers = self.start(CLAIM, 4)
        self.hold_write_lock()

        claimed = self.results(workers)

        self.assert_distinct_leases(claimed)

    def test_processes_draining_the_queue(self) -> None:
        self.jobs |= {self.queue.enqueue('task', index) for index in range(2, 400)}
        workers = self.start(DRAIN, 6)
        self.hold_write_lock()

        claimed = [job_id for result in self.results(workers) for job_id in result]

        self.assert_distinct_leases(claimed)

    def test_tokens_identify_one_lease(self) -> None:
        first = self.queue.claim('w1')
        second = self.queue.claim('w2')

        assert isinstance(first, Lease) and isinstance(second, Lease)
        self.assertNotEqual(first.token, second.token)
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertIsNone(self.queue.claim('w3'))
