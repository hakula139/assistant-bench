import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jobq import Lease, LeaseLost, Queue, Worker


class FakeClock:
    def __init__(self) -> None:
        self.now = 50_000.0

    def __call__(self) -> float:
        return self.now


def open_queue(clock: FakeClock, **options: Any) -> Queue:
    directory = tempfile.mkdtemp()
    return Queue(Path(directory) / 'jobs.db', clock=clock, **options)


def lease(lease: Lease | None) -> dict[str, Any] | None:
    return None if lease is None else dataclasses.asdict(lease)


def job(queue: Queue, job_id: int) -> dict[str, Any]:
    return dataclasses.asdict(queue.get(job_id))


def lost(action: Callable[[], object]) -> bool:
    """Tell whether `action` raised `LeaseLost`."""
    try:
        action()
    except LeaseLost:
        return True
    return False


def expired_leases_are_claimed_again() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock)
    job_id = queue.enqueue('task')
    first = queue.claim('w1')
    clock.now += 29.5
    early = queue.claim('w2')
    clock.now += 0.5
    second = queue.claim('w2')
    return {
        'job_id': job_id,
        'first': lease(first),
        'early': lease(early),
        'second': lease(second),
        'job': job(queue, job_id),
    }


def superseded_leases_change_nothing() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock)
    job_id = queue.enqueue('task')
    stale = queue.claim('w1')
    clock.now += 31
    current = queue.claim('w2')
    assert stale is not None and current is not None
    lost_actions = [
        lost(lambda: queue.complete(stale, 'from w1')),
        lost(lambda: queue.fail(stale, 'w1 failed')),
        lost(lambda: queue.heartbeat(stale)),
    ]
    before = job(queue, job_id)
    queue.complete(current, 'from w2')
    return {'lost': lost_actions, 'before': before, 'after': job(queue, job_id)}


def expired_leases_cannot_be_used() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock)
    job_id = queue.enqueue('task')
    expired = queue.claim('w1', lease_seconds=10)
    assert expired is not None
    clock.now += 10
    lost_actions = [
        lost(lambda: queue.heartbeat(expired)),
        lost(lambda: queue.complete(expired, 'late')),
        lost(lambda: queue.fail(expired, 'late failure')),
    ]
    return {
        'job_id': job_id,
        'lost': lost_actions,
        'job': job(queue, job_id),
        'next': lease(queue.claim('w2')),
    }


def claim_order() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock)
    later = queue.enqueue('task', delay=10)
    earlier = queue.enqueue('task')
    tied = queue.enqueue('task', delay=10)
    clock.now += 10
    order = [lease(queue.claim('w1')) for _ in range(3)]
    return {'later': later, 'earlier': earlier, 'tied': tied, 'order': order}


def open_current_format(schema: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Open a database that `schema` and `rows` wrote, then use it.

    Each row's `run_at`, `lease_until` and `finished_at` are seconds after the
    clock's start.
    """
    clock = FakeClock()
    path = Path(tempfile.mkdtemp()) / 'jobs.db'
    db = sqlite3.connect(path)
    db.executescript(schema)
    for row in rows:
        row = {
            name: clock.now + value
            if name in {'run_at', 'created_at', 'lease_until', 'finished_at'}
            else value
            for name, value in row.items()
        }
        columns, values = ', '.join(row), ', '.join('?' * len(row))
        db.execute(
            f'INSERT INTO jobs ({columns}) VALUES ({values})', tuple(row.values())
        )
    db.commit()
    db.close()

    try:
        queue = Queue(path, clock=clock)
    except Exception as error:
        return {'error': repr(error)}
    done = job(queue, 4)
    dead = [(dead.id, dead.last_error) for dead in queue.dead_letters()]
    first = queue.claim('w1')
    second = queue.claim('w2')
    third = queue.claim('w3')
    held = job(queue, 3)
    enqueued = [
        queue.enqueue(kind, dedupe_key=key)
        for kind, key in (('ready', 'q'), ('held', 'r'), ('finished', 'd'))
    ]
    enqueued.append(queue.enqueue('broken', dedupe_key='x'))
    assert first is not None
    queue.complete(first, 'one')
    return {
        'error': None,
        'done': done,
        'dead': dead,
        'first': lease(first),
        'second': lease(second),
        'third': lease(third),
        'held': held,
        'enqueued': enqueued,
        'counts': queue.counts(),
    }


def heartbeats_keep_the_lease() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock)
    job_id = queue.enqueue('task')
    held = queue.claim('w1', lease_seconds=10)
    assert held is not None
    clock.now += 8
    held = queue.heartbeat(held, lease_seconds=10)
    clock.now += 8
    other = queue.claim('w2')
    queue.complete(held, 'kept')
    return {'other': lease(other), 'job': job(queue, job_id)}


def leases_keep_expiring() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock, max_attempts=3)
    job_id = queue.enqueue('poison')
    other = queue.enqueue('fine', delay=1000)
    attempts: list[dict[str, Any] | None] = []
    for attempt in (1, 2, 3):
        attempts.append(lease(queue.claim(f'w{attempt}')))
        clock.now += 31
    fourth = queue.claim('w4')
    poisoned = job(queue, job_id)
    dead = [dead.id for dead in queue.dead_letters()]
    clock.now += 1000
    return {
        'job_id': job_id,
        'other': other,
        'attempts': attempts,
        'fourth': lease(fourth),
        'job': poisoned,
        'dead': dead,
        'last': lease(queue.claim('w5')),
    }


def failures_back_off(delays: list[float]) -> dict[str, Any]:
    """Fail the job once per delay, checking the queue just before it ends."""
    clock = FakeClock()
    queue = open_queue(clock, max_attempts=4, retry_delay=2, max_retry_delay=5)
    job_id = queue.enqueue('flaky')
    retries: list[dict[str, Any]] = []
    for delay in delays:
        held = queue.claim('w1')
        assert held is not None
        failed_at = clock.now
        failed = queue.fail(held, f'attempt {held.attempt}')
        clock.now = failed_at + delay - 0.01
        early = queue.claim('w1')
        clock.now = failed_at + delay
        retries.append(
            {
                'status': failed.status,
                'wait': failed.run_at - failed_at,
                'early': lease(early),
            }
        )
    held = queue.claim('w1')
    assert held is not None
    final = dataclasses.asdict(queue.fail(held, 'attempt 4'))
    return {
        'retries': retries,
        'final': final,
        'after': lease(queue.claim('w1')),
        'attempts': queue.get(job_id).attempts,
    }


def dedupe_keys_are_freed() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock, max_attempts=1)
    first = queue.enqueue('sync', dedupe_key='k')
    held = queue.claim('w1')
    assert held is not None
    while_running = queue.enqueue('sync', dedupe_key='k')
    queue.complete(held)
    second = queue.enqueue('sync', dedupe_key='k')
    held = queue.claim('w1')
    assert held is not None
    queue.fail(held, 'gone')
    third = queue.enqueue('sync', dedupe_key='k')
    return {
        'first': first,
        'while_running': while_running,
        'second': second,
        'third': third,
        'third_status': queue.get(third).status,
        'again': queue.enqueue('sync', dedupe_key='k'),
    }


def workers_drop_outlived_jobs() -> dict[str, Any]:
    clock = FakeClock()
    queue = open_queue(clock)
    job_id = queue.enqueue('slow')

    def slow(_: object) -> str:
        clock.now += 60
        return 'too late'

    ran = Worker(queue, {'slow': slow}, name='w1', lease_seconds=30).run_once()
    return {
        'job_id': job_id,
        'ran': ran,
        'job': job(queue, job_id),
        'next': lease(queue.claim('w2')),
    }


def cli_workers_run_every_job(numbers: list[int]) -> dict[str, Any]:
    directory = Path(tempfile.mkdtemp())
    path = directory / 'jobs.db'
    (directory / 'handlers.py').write_text('HANDLERS = {"square": lambda n: n * n}\n')
    env = {**os.environ, 'PYTHONPATH': f'{directory}{os.pathsep}{Path.cwd()}'}

    def jobq(*args: str) -> str:
        return subprocess.run(
            [sys.executable, '-m', 'jobq', str(path), *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout

    ids = [int(jobq('enqueue', 'square', str(n))) for n in numbers]
    ran = int(jobq('work', 'handlers', '--name', 'cli', '--until-idle'))
    results = [json.loads(jobq('status', str(job_id)))['result'] for job_id in ids]
    return {'ran': ran, 'results': results}
