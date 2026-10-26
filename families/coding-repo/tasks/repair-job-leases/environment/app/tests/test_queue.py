import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jobq import Queue, UnknownJob, Worker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class QueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'jobs.db'
        self.clock = FakeClock()
        self.queue = self.enterContext(Queue(self.path, clock=self.clock))

    def test_claims_ready_jobs_in_order(self) -> None:
        later = self.queue.enqueue('email', {'to': 'b'}, delay=5)
        first = self.queue.enqueue('email', {'to': 'a'})

        lease = self.queue.claim('w1')

        assert lease is not None
        self.assertEqual(
            (lease.job_id, lease.payload, lease.attempt), (first, {'to': 'a'}, 1)
        )
        self.assertIsNone(self.queue.claim('w2'))
        self.clock.now += 5
        self.assertEqual(getattr(self.queue.claim('w2'), 'job_id', None), later)

    def test_complete_stores_the_result(self) -> None:
        job_id = self.queue.enqueue('resize', [1, 2])
        lease = self.queue.claim('w1')
        assert lease is not None

        self.queue.complete(lease, {'ok': True})

        job = self.queue.get(job_id)
        self.assertEqual((job.status, job.result), ('done', {'ok': True}))
        self.assertEqual(self.queue.counts()['done'], 1)

    def test_last_failed_attempt_dead_letters_the_job(self) -> None:
        queue = self.enterContext(Queue(self.path, clock=self.clock, max_attempts=1))
        job_id = queue.enqueue('flaky')
        lease = queue.claim('w1')
        assert lease is not None

        job = queue.fail(lease, 'boom')

        self.assertEqual((job.status, job.last_error), ('dead', 'boom'))
        self.assertEqual([dead.id for dead in queue.dead_letters()], [job_id])

    def test_dedupe_key_returns_the_queued_job(self) -> None:
        first = self.queue.enqueue('sync', dedupe_key='account-7')

        second = self.queue.enqueue('sync', dedupe_key='account-7')

        self.assertEqual(first, second)
        self.assertEqual(self.queue.counts()['queued'], 1)

    def test_heartbeat_extends_the_lease(self) -> None:
        self.queue.enqueue('long')
        lease = self.queue.claim('w1', lease_seconds=10)
        assert lease is not None

        self.clock.now += 5
        extended = self.queue.heartbeat(lease, lease_seconds=10)

        self.assertEqual(extended.expires_at, self.clock.now + 10)

    def test_unknown_jobs_raise(self) -> None:
        with self.assertRaises(UnknownJob):
            self.queue.get(42)

    def test_worker_runs_handlers(self) -> None:
        ok = self.queue.enqueue('double', 21)
        broken = self.queue.enqueue('explode')
        worker = Worker(
            self.queue,
            {'double': lambda n: n * 2, 'explode': lambda _: 1 / 0},
            name='w1',
        )

        processed = worker.run(until_idle=True)

        self.assertEqual(processed, 2)
        self.assertEqual(self.queue.get(ok).result, 42)
        self.assertIn('ZeroDivisionError', self.queue.get(broken).last_error or '')

    def test_cli_round_trip(self) -> None:
        def jobq(*args: str) -> str:
            return subprocess.run(
                [sys.executable, '-m', 'jobq', str(self.path), *args],
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        job_id = jobq('enqueue', 'email', '{"to": "a"}').strip()

        status = json.loads(jobq('status', job_id))
        self.assertEqual((status['kind'], status['payload']), ('email', {'to': 'a'}))
        self.assertEqual(json.loads(jobq('counts'))['queued'], 1)
