import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jobq import Lease, LeaseLost, Queue


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class LeaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'jobs.db'
        self.clock = FakeClock()
        self.queue = self.enterContext(
            Queue(self.path, clock=self.clock, max_attempts=2, retry_delay=3)
        )

    def claim(self, worker: str) -> Lease:
        lease = self.queue.claim(worker, lease_seconds=10)
        assert lease is not None
        return lease

    def test_claims_waiting_on_the_write_lock_do_not_share_a_job(self) -> None:
        self.queue.enqueue('task')
        queues = [self.enterContext(Queue(self.path)) for _ in range(4)]
        leases: list[Lease | None] = []
        blocker = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(blocker.close)

        def claim(queue: Queue) -> None:
            leases.append(queue.claim('w'))

        threads = [threading.Thread(target=claim, args=(queue,)) for queue in queues]

        blocker.execute('BEGIN IMMEDIATE')
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        blocker.execute('COMMIT')
        for thread in threads:
            thread.join(15)

        self.assertEqual(len([lease for lease in leases if lease is not None]), 1)

    def test_expired_leases_are_reclaimed_and_fenced(self) -> None:
        job_id = self.queue.enqueue('task')
        stale = self.claim('w1')
        self.clock.now += 10

        current = self.claim('w2')

        self.assertEqual((current.job_id, current.attempt), (job_id, 2))
        with self.assertRaises(LeaseLost):
            self.queue.complete(stale, 'stale')
        self.queue.complete(current, 'fresh')
        self.assertEqual(self.queue.get(job_id).result, 'fresh')

    def test_expired_leases_cannot_be_extended(self) -> None:
        self.queue.enqueue('task')
        lease = self.claim('w1')
        self.clock.now += 10

        with self.assertRaises(LeaseLost):
            self.queue.heartbeat(lease)

    def test_expiring_on_the_last_attempt_dead_letters(self) -> None:
        job_id = self.queue.enqueue('poison')
        for worker in ('w1', 'w2'):
            self.claim(worker)
            self.clock.now += 10

        self.assertIsNone(self.queue.claim('w3'))
        job = self.queue.get(job_id)
        self.assertEqual((job.status, job.last_error), ('dead', 'lease expired'))

    def test_retries_wait_on_the_queue_clock(self) -> None:
        job_id = self.queue.enqueue('flaky')

        job = self.queue.fail(self.claim('w1'), 'boom')

        self.assertEqual((job.status, job.run_at), ('queued', self.clock.now + 3))
        self.clock.now += 3
        self.assertEqual(self.claim('w1').job_id, job_id)

    def test_backoff_stays_at_the_cap_for_late_attempts(self) -> None:
        delays = [self.queue.backoff(attempt) for attempt in (1, 8, 1025, 5000)]

        self.assertEqual(delays, [3, 300, 300, 300])

    def test_finished_jobs_free_their_dedupe_key(self) -> None:
        first = self.queue.enqueue('sync', dedupe_key='k')
        self.queue.complete(self.claim('w1'))

        second = self.queue.enqueue('sync', dedupe_key='k')

        self.assertNotEqual(first, second)
        self.assertEqual(self.queue.get(second).status, 'queued')
