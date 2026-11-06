"""A durable job queue whose workers hold jobs through expiring leases."""

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self

from .database import connect, transaction
from .errors import LeaseLost, UnknownJob


type Clock = Callable[[], float]

EXPIRED_ERROR = 'lease expired'


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    payload: Any
    status: str
    """One of `queued`, `running`, `done` and `dead`."""

    attempts: int
    run_at: float
    dedupe_key: str | None
    result: Any
    last_error: str | None


@dataclass(frozen=True)
class Lease:
    job_id: int
    kind: str
    payload: Any
    worker: str
    token: str
    attempt: int
    expires_at: float


class Queue:
    def __init__(
        self,
        path: Path,
        *,
        clock: Clock = time.time,
        max_attempts: int = 5,
        retry_delay: float = 1.0,
        max_retry_delay: float = 300.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError('max_attempts must be at least 1.')
        self.path = path
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        self._clock = clock
        self._db = connect(path)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def enqueue(
        self,
        kind: str,
        payload: Any = None,
        *,
        dedupe_key: str | None = None,
        delay: float = 0.0,
    ) -> int:
        """Add a job and return its ID, or the ID of an active job with `dedupe_key`."""
        encoded = json.dumps(payload)
        with transaction(self._db) as db:
            if dedupe_key is not None:
                row = db.execute(
                    """
                    SELECT id FROM jobs
                    WHERE dedupe_key = ? AND status IN ('queued', 'running')
                    """,
                    (dedupe_key,),
                ).fetchone()
                if row is not None:
                    return row['id']

            now = self._clock()
            cursor = db.execute(
                """
                INSERT INTO jobs (kind, payload, dedupe_key, status, run_at, created_at)
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (kind, encoded, dedupe_key, now + delay, now),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def claim(self, worker: str, *, lease_seconds: float = 30.0) -> Lease | None:
        """Lease the next ready job to `worker`, or return `None` when none is ready."""
        with transaction(self._db) as db:
            now = self._clock()
            self._expire(db, now)
            row = db.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued' AND run_at <= ?
                ORDER BY run_at, id
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None

            lease = Lease(
                job_id=row['id'],
                kind=row['kind'],
                payload=json.loads(row['payload']),
                worker=worker,
                token=secrets.token_hex(16),
                attempt=row['attempts'] + 1,
                expires_at=now + lease_seconds,
            )
            db.execute(
                """
                UPDATE jobs
                SET status = 'running', attempts = ?, lease_owner = ?,
                    lease_token = ?, lease_until = ?
                WHERE id = ?
                """,
                (lease.attempt, worker, lease.token, lease.expires_at, lease.job_id),
            )
        return lease

    def heartbeat(self, lease: Lease, *, lease_seconds: float = 30.0) -> Lease:
        """Extend a held lease and return it with its new expiry."""
        with transaction(self._db) as db:
            now = self._clock()
            self._check(db, lease, now)
            extended = replace(lease, expires_at=now + lease_seconds)
            db.execute(
                'UPDATE jobs SET lease_until = ? WHERE id = ?',
                (extended.expires_at, lease.job_id),
            )
        return extended

    def complete(self, lease: Lease, result: Any = None) -> None:
        with transaction(self._db) as db:
            now = self._clock()
            self._check(db, lease, now)
            db.execute(
                """
                UPDATE jobs
                SET status = 'done', result = ?, finished_at = ?,
                    lease_owner = NULL, lease_token = NULL, lease_until = NULL
                WHERE id = ?
                """,
                (json.dumps(result), now, lease.job_id),
            )

    def fail(self, lease: Lease, error: str) -> Job:
        """Record a failed attempt, then retry the job later or dead-letter it."""
        with transaction(self._db) as db:
            now = self._clock()
            self._check(db, lease, now)
            if lease.attempt >= self.max_attempts:
                db.execute(
                    """
                    UPDATE jobs
                    SET status = 'dead', last_error = ?, finished_at = ?,
                        lease_owner = NULL, lease_token = NULL, lease_until = NULL
                    WHERE id = ?
                    """,
                    (error, now, lease.job_id),
                )
            else:
                db.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', last_error = ?, run_at = ?,
                        lease_owner = NULL, lease_token = NULL, lease_until = NULL
                    WHERE id = ?
                    """,
                    (error, now + self.backoff(lease.attempt), lease.job_id),
                )
        return self.get(lease.job_id)

    def backoff(self, attempt: int) -> float:
        """Seconds to wait before retrying a job whose `attempt` failed."""
        return min(self.retry_delay * 2 ** (attempt - 1), self.max_retry_delay)

    def get(self, job_id: int) -> Job:
        row = self._db.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if row is None:
            raise UnknownJob(job_id)
        return _job(row)

    def dead_letters(self) -> list[Job]:
        rows = self._db.execute(
            "SELECT * FROM jobs WHERE status = 'dead' ORDER BY finished_at, id"
        )
        return [_job(row) for row in rows]

    def counts(self) -> dict[str, int]:
        """Count jobs by status, with every status present."""
        counts: dict[str, int] = dict.fromkeys(('queued', 'running', 'done', 'dead'), 0)
        rows = self._db.execute(
            'SELECT status, count(*) AS n FROM jobs GROUP BY status'
        )
        counts.update({row['status']: row['n'] for row in rows})
        return counts

    def _expire(self, db: sqlite3.Connection, now: float) -> None:
        """Turn expired leases into failed attempts."""
        db.execute(
            """
            UPDATE jobs
            SET status = CASE WHEN attempts >= ? THEN 'dead' ELSE 'queued' END,
                finished_at = CASE WHEN attempts >= ? THEN ? END,
                last_error = ?,
                lease_owner = NULL, lease_token = NULL, lease_until = NULL
            WHERE status = 'running' AND lease_until <= ?
            """,
            (self.max_attempts, self.max_attempts, now, EXPIRED_ERROR, now),
        )

    def _check(self, db: sqlite3.Connection, lease: Lease, now: float) -> None:
        row = db.execute(
            """
            SELECT 1 FROM jobs
            WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_until > ?
            """,
            (lease.job_id, lease.token, now),
        ).fetchone()
        if row is None:
            raise LeaseLost(lease.job_id)


def _job(row: sqlite3.Row) -> Job:
    result = row['result']
    return Job(
        id=row['id'],
        kind=row['kind'],
        payload=json.loads(row['payload']),
        status=row['status'],
        attempts=row['attempts'],
        run_at=row['run_at'],
        dedupe_key=row['dedupe_key'],
        result=None if result is None else json.loads(result),
        last_error=row['last_error'],
    )
