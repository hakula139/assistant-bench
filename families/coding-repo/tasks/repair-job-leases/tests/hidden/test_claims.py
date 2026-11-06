import json
import subprocess
import unittest
from typing import Any

import sandbox


ROUNDS = 5
"""Races per test, each over two fresh jobs."""


class ConcurrentClaimTest(unittest.TestCase):
    """Claims start together and race, or queue up behind a write lock."""

    def setUp(self) -> None:
        self.path = str(sandbox.scratch() / 'jobs.db')
        sandbox.observe('claims', 'enqueue', path=self.path, count=0)

    def enqueue(self, count: int) -> set[int]:
        return set(sandbox.observe('claims', 'enqueue', path=self.path, count=count))

    def assert_distinct_leases(
        self, claimed: list[int | None], jobs: set[int], attempt: int = 0
    ) -> None:
        leased = [job_id for job_id in claimed if job_id is not None]
        self.assertEqual(sorted(leased), sorted(jobs), f'round {attempt}: {claimed}')

    def test_threads(self) -> None:
        rounds = sandbox.observe('claims', 'threads', rounds=ROUNDS, workers=6)

        for attempt, race in enumerate(rounds):
            self.assertEqual(race['errors'], [], f'round {attempt}')
            self.assert_distinct_leases(race['claimed'], set(race['jobs']), attempt)

    def start(self, function: str, count: int) -> list[subprocess.Popen[str]]:
        """Start `count` workers running `function` and wait until each is ready."""
        workers = [
            sandbox.start('claims', function, path=self.path, worker=f'p{index}')
            for index in range(count)
        ]
        for worker in workers:
            self.addCleanup(worker.kill)
            assert worker.stdout is not None
            self.assertEqual(worker.stdout.readline(), 'ready\n')
        return workers

    def release_workers(
        self, workers: list[subprocess.Popen[str]], lock: subprocess.Popen[str] | None
    ) -> list[Any]:
        """Let each worker run one step and read its result.

        With a `lock` holder, the step waits behind the write lock until every
        worker has started it.
        """
        for worker in workers:
            assert worker.stdin is not None
            worker.stdin.write('go\n')
            worker.stdin.flush()
        if lock is not None:
            assert lock.stdin is not None
            lock.stdin.write('release\n')
            lock.stdin.flush()

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
        workers = self.start('claim_on_request', 4)

        for attempt in range(ROUNDS):
            jobs = self.enqueue(2)
            claimed = self.release_workers(workers, None)

            self.assert_distinct_leases(claimed, jobs, attempt)
        self.finish(workers)

    def test_processes_draining_the_queue(self) -> None:
        jobs = self.enqueue(100)
        workers = self.start('drain_on_request', 4)
        lock = sandbox.start('claims', 'hold_write_lock', path=self.path)
        self.addCleanup(lock.kill)
        assert lock.stdout is not None
        self.assertEqual(lock.stdout.readline(), 'locked\n')

        claimed = [
            job_id
            for result in self.release_workers(workers, lock)
            for job_id in result
        ]

        self.finish(workers)
        self.assert_distinct_leases(claimed, jobs)

    def test_tokens_identify_one_lease(self) -> None:
        seen = sandbox.observe('claims', 'distinct_tokens')

        (first_id, first_token), (second_id, second_token) = (
            seen['first'],
            seen['second'],
        )
        self.assertNotEqual(first_token, second_token)
        self.assertNotEqual(first_id, second_id)
        self.assertIsNone(seen['third'])
