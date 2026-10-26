import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from jobq import Lease, LeaseLost, Queue, Worker


class FakeClock:
    def __init__(self) -> None:
        self.now = 50_000.0

    def __call__(self) -> float:
        return self.now


class LeaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.directory / 'jobs.db'
        self.clock = FakeClock()

    def open(self, **options: Any) -> Queue:
        return self.enterContext(Queue(self.path, clock=self.clock, **options))

    def claim(self, queue: Queue, worker: str, lease_seconds: float = 30) -> Lease:
        lease = queue.claim(worker, lease_seconds=lease_seconds)
        if lease is None:
            self.fail(f'{worker} found no ready job')
        return lease

    def test_expired_leases_are_claimed_again(self) -> None:
        queue = self.open()
        job_id = queue.enqueue('task')
        first = self.claim(queue, 'w1')
        self.clock.now += 29.5
        self.assertIsNone(queue.claim('w2'))

        self.clock.now += 0.5
        second = self.claim(queue, 'w2')

        self.assertEqual((second.job_id, second.attempt), (job_id, 2))
        self.assertNotEqual(second.token, first.token)
        job = queue.get(job_id)
        self.assertEqual((job.status, job.attempts), ('running', 2))
        self.assertEqual(job.last_error, 'lease expired')

    def test_superseded_leases_change_nothing(self) -> None:
        queue = self.open()
        job_id = queue.enqueue('task')
        stale = self.claim(queue, 'w1')
        self.clock.now += 31
        current = self.claim(queue, 'w2')

        for action in (
            lambda: queue.complete(stale, 'from w1'),
            lambda: queue.fail(stale, 'w1 failed'),
            lambda: queue.heartbeat(stale),
        ):
            with self.assertRaises(LeaseLost):
                action()

        job = queue.get(job_id)
        self.assertEqual((job.status, job.result, job.attempts), ('running', None, 2))
        queue.complete(current, 'from w2')
        self.assertEqual(queue.get(job_id).result, 'from w2')

    def test_expired_leases_cannot_be_used(self) -> None:
        queue = self.open()
        job_id = queue.enqueue('task')
        lease = self.claim(queue, 'w1', lease_seconds=10)
        self.clock.now += 10

        with self.assertRaises(LeaseLost):
            queue.heartbeat(lease)
        with self.assertRaises(LeaseLost):
            queue.complete(lease, 'late')

        self.assertNotEqual(queue.get(job_id).status, 'done')
        self.assertEqual(self.claim(queue, 'w2').job_id, job_id)

    def test_heartbeats_keep_the_lease(self) -> None:
        queue = self.open()
        job_id = queue.enqueue('task')
        lease = self.claim(queue, 'w1', lease_seconds=10)

        self.clock.now += 8
        lease = queue.heartbeat(lease, lease_seconds=10)
        self.clock.now += 8

        self.assertIsNone(queue.claim('w2'))
        queue.complete(lease, 'kept')
        self.assertEqual(queue.get(job_id).status, 'done')

    def test_jobs_whose_leases_keep_expiring_are_dead_lettered(self) -> None:
        queue = self.open(max_attempts=3)
        job_id = queue.enqueue('poison')
        other = queue.enqueue('fine', delay=1000)
        for attempt in (1, 2, 3):
            self.assertEqual(self.claim(queue, f'w{attempt}').attempt, attempt)
            self.clock.now += 31

        self.assertIsNone(queue.claim('w4'))

        job = queue.get(job_id)
        self.assertEqual((job.status, job.attempts), ('dead', 3))
        self.assertEqual(job.last_error, 'lease expired')
        self.assertEqual([dead.id for dead in queue.dead_letters()], [job_id])
        self.clock.now += 1000
        self.assertEqual(self.claim(queue, 'w5').job_id, other)

    def test_failures_back_off_on_the_queue_clock(self) -> None:
        queue = self.open(max_attempts=4, retry_delay=2, max_retry_delay=5)
        job_id = queue.enqueue('flaky')
        for delay in (2, 4, 5):
            lease = self.claim(queue, 'w1')
            failed_at = self.clock.now

            job = queue.fail(lease, f'attempt {lease.attempt}')

            self.assertEqual((job.status, job.run_at), ('queued', failed_at + delay))
            self.clock.now = failed_at + delay - 0.01
            self.assertIsNone(queue.claim('w1'))
            self.clock.now = failed_at + delay

        lease = self.claim(queue, 'w1')
        job = queue.fail(lease, 'attempt 4')
        self.assertEqual((job.status, job.last_error), ('dead', 'attempt 4'))
        self.assertIsNone(queue.claim('w1'))
        self.assertEqual(queue.get(job_id).attempts, 4)

    def test_dedupe_keys_are_freed_by_finished_jobs(self) -> None:
        queue = self.open(max_attempts=1)
        first = queue.enqueue('sync', dedupe_key='k')
        lease = self.claim(queue, 'w1')
        self.assertEqual(queue.enqueue('sync', dedupe_key='k'), first)
        queue.complete(lease)

        second = queue.enqueue('sync', dedupe_key='k')
        lease = self.claim(queue, 'w1')
        queue.fail(lease, 'gone')
        third = queue.enqueue('sync', dedupe_key='k')

        self.assertEqual(len({first, second, third}), 3)
        self.assertEqual(queue.get(third).status, 'queued')
        self.assertEqual(queue.enqueue('sync', dedupe_key='k'), third)

    def test_workers_drop_jobs_they_outlived(self) -> None:
        queue = self.open()
        job_id = queue.enqueue('slow')

        def slow(_: object) -> str:
            self.clock.now += 60
            return 'too late'

        ran = Worker(queue, {'slow': slow}, name='w1', lease_seconds=30).run_once()

        self.assertTrue(ran)
        self.assertNotEqual(queue.get(job_id).status, 'done')
        lease = self.claim(queue, 'w2')
        self.assertEqual((lease.job_id, lease.attempt), (job_id, 2))

    def test_cli_workers_run_every_job(self) -> None:
        (self.directory / 'handlers.py').write_text(
            'HANDLERS = {"square": lambda n: n * n}\n'
        )
        env = {**os.environ, 'PYTHONPATH': f'{self.directory}{os.pathsep}{Path.cwd()}'}

        def jobq(*args: str) -> str:
            return subprocess.run(
                [sys.executable, '-m', 'jobq', str(self.path), *args],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            ).stdout

        ids = [int(jobq('enqueue', 'square', str(n))) for n in range(3)]
        ran = jobq('work', 'handlers', '--name', 'cli', '--until-idle')

        self.assertEqual(int(ran), 3)
        self.assertEqual(
            [json.loads(jobq('status', str(job_id)))['result'] for job_id in ids],
            [0, 1, 4],
        )
