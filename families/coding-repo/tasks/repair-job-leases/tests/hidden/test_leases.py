import unittest
from typing import Any

from sandbox import observe


STARTER_SCHEMA = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    dedupe_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    run_at REAL NOT NULL,
    lease_owner TEXT,
    lease_token TEXT,
    lease_until REAL,
    result TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX jobs_ready ON jobs (status, run_at, id);
CREATE UNIQUE INDEX jobs_active_dedupe ON jobs (dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');
PRAGMA user_version = 1;
"""
"""The database format of the version operators already run."""


class LeaseTest(unittest.TestCase):
    def claimed(self, lease: dict[str, Any] | None, worker: str) -> dict[str, Any]:
        if lease is None:
            self.fail(f'{worker} found no ready job')
        return lease

    def test_expired_leases_are_claimed_again(self) -> None:
        seen = observe('leases', 'expired_leases_are_claimed_again')

        first = self.claimed(seen['first'], 'w1')
        self.assertIsNone(seen['early'])
        second = self.claimed(seen['second'], 'w2')
        self.assertEqual((second['job_id'], second['attempt']), (seen['job_id'], 2))
        self.assertNotEqual(second['token'], first['token'])
        job = seen['job']
        self.assertEqual((job['status'], job['attempts']), ('running', 2))
        self.assertEqual(job['last_error'], 'lease expired')

    def test_superseded_leases_change_nothing(self) -> None:
        seen = observe('leases', 'superseded_leases_change_nothing')

        self.assertEqual(seen['lost'], [True, True, True])
        before = seen['before']
        self.assertEqual(
            (before['status'], before['result'], before['attempts']),
            ('running', None, 2),
        )
        self.assertEqual(seen['after']['result'], 'from w2')

    def test_expired_leases_cannot_be_used(self) -> None:
        seen = observe('leases', 'expired_leases_cannot_be_used')

        self.assertEqual(seen['lost'], [True, True, True])
        job = seen['job']
        self.assertIn(job['status'], {'queued', 'running'})
        self.assertEqual((job['attempts'], job['result']), (1, None))
        self.assertNotEqual(job['last_error'], 'late failure')
        self.assertEqual(self.claimed(seen['next'], 'w2')['job_id'], seen['job_id'])

    def test_claims_take_the_earliest_run_at_then_the_lowest_id(self) -> None:
        seen = observe('leases', 'claim_order')

        order = [self.claimed(lease, 'w1')['job_id'] for lease in seen['order']]
        self.assertEqual(order, [seen['earlier'], seen['later'], seen['tied']])

    def test_opens_queues_written_by_the_current_version(self) -> None:
        rows: list[dict[str, Any]] = [
            {'kind': 'ready', 'payload': '[1]', 'dedupe_key': 'q', 'run_at': -10},
            {'kind': 'later', 'payload': '{}', 'run_at': 100},
            {
                'kind': 'held',
                'payload': 'null',
                'dedupe_key': 'r',
                'status': 'running',
                'attempts': 1,
                'run_at': -5,
                'lease_owner': 'gone',
                'lease_token': 'stale',
                'lease_until': -1,
            },
            {
                'kind': 'finished',
                'payload': '"x"',
                'dedupe_key': 'd',
                'status': 'done',
                'attempts': 1,
                'result': '{"ok": true}',
                'finished_at': -25,
            },
            {
                'kind': 'broken',
                'payload': '2',
                'dedupe_key': 'x',
                'status': 'dead',
                'attempts': 5,
                'last_error': 'boom',
                'finished_at': -25,
            },
        ]
        rows = [
            {'id': job_id, 'status': 'queued', 'run_at': -30, 'created_at': -40, **row}
            for job_id, row in enumerate(rows, 1)
        ]

        seen = observe(
            'leases', 'open_current_format', schema=STARTER_SCHEMA, rows=rows
        )

        if seen['error'] is not None:
            self.fail(f'a queue in the current format fails to open: {seen["error"]}')
        done = seen['done']
        self.assertEqual(
            (done['status'], done['result'], done['payload']),
            ('done', {'ok': True}, 'x'),
        )
        self.assertEqual(seen['dead'], [[5, 'boom']])
        first = self.claimed(seen['first'], 'w1')
        second = self.claimed(seen['second'], 'w2')
        self.assertIsNone(seen['third'])
        self.assertEqual(
            (first['job_id'], first['payload'], first['attempt']), (1, [1], 1)
        )
        self.assertEqual((second['job_id'], second['attempt']), (3, 2))
        self.assertEqual(seen['held']['last_error'], 'lease expired')
        ready, held, finished, broken = seen['enqueued']
        self.assertEqual((ready, held), (1, 3))
        self.assertNotIn(finished, {1, 2, 3, 4, 5})
        self.assertNotIn(broken, {1, 2, 3, 4, 5})
        self.assertEqual(
            seen['counts'], {'queued': 3, 'running': 1, 'done': 2, 'dead': 1}
        )

    def test_heartbeats_keep_the_lease(self) -> None:
        seen = observe('leases', 'heartbeats_keep_the_lease')

        self.assertIsNone(seen['other'])
        self.assertEqual(seen['job']['status'], 'done')

    def test_jobs_whose_leases_keep_expiring_are_dead_lettered(self) -> None:
        seen = observe('leases', 'leases_keep_expiring')

        attempts = [
            self.claimed(lease, f'w{attempt}')['attempt']
            for attempt, lease in enumerate(seen['attempts'], 1)
        ]
        self.assertEqual(attempts, [1, 2, 3])
        self.assertIsNone(seen['fourth'])
        job = seen['job']
        self.assertEqual((job['status'], job['attempts']), ('dead', 3))
        self.assertEqual(job['last_error'], 'lease expired')
        self.assertEqual(seen['dead'], [seen['job_id']])
        self.assertEqual(self.claimed(seen['last'], 'w5')['job_id'], seen['other'])

    def test_failures_back_off_on_the_queue_clock(self) -> None:
        delays = [2, 4, 5]

        seen = observe('leases', 'failures_back_off', delays=delays)

        for delay, retry in zip(delays, seen['retries'], strict=True):
            with self.subTest(delay=delay):
                self.assertEqual((retry['status'], retry['wait']), ('queued', delay))
                self.assertIsNone(retry['early'])
        final = seen['final']
        self.assertEqual((final['status'], final['last_error']), ('dead', 'attempt 4'))
        self.assertIsNone(seen['after'])
        self.assertEqual(seen['attempts'], 4)

    def test_dedupe_keys_are_freed_by_finished_jobs(self) -> None:
        seen = observe('leases', 'dedupe_keys_are_freed')

        self.assertEqual(seen['while_running'], seen['first'])
        self.assertEqual(len({seen['first'], seen['second'], seen['third']}), 3)
        self.assertEqual(seen['third_status'], 'queued')
        self.assertEqual(seen['again'], seen['third'])

    def test_workers_drop_jobs_they_outlived(self) -> None:
        seen = observe('leases', 'workers_drop_outlived_jobs')

        self.assertIs(seen['ran'], True)
        self.assertNotEqual(seen['job']['status'], 'done')
        lease = self.claimed(seen['next'], 'w2')
        self.assertEqual((lease['job_id'], lease['attempt']), (seen['job_id'], 2))

    def test_cli_workers_run_every_job(self) -> None:
        numbers = [0, 1, 2]

        seen = observe('leases', 'cli_workers_run_every_job', numbers=numbers)

        self.assertEqual(seen['ran'], 3)
        self.assertEqual(seen['results'], [n * n for n in numbers])
