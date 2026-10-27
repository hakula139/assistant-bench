# jobq

`jobq` is a durable job queue stored in one SQLite file. Any number of processes may open the same file, enqueue jobs and work on them, and workers hold each job through an expiring lease, so a job whose worker dies runs again elsewhere.

## Jobs

`Queue(path, *, clock=time.time, max_attempts=5, retry_delay=1.0, max_retry_delay=300.0)` opens or creates the queue at `path`. Every time the queue records or compares comes from `clock`, a function returning seconds.

`enqueue(kind, payload=None, *, dedupe_key=None, delay=0.0)` adds a job of `kind` with a JSON-serializable `payload` that becomes ready `delay` seconds from now, and returns its ID. While a job with the same `dedupe_key` is queued or running, `enqueue` adds nothing and returns that job's ID, and once it is done or dead the key is free again.

A job is `queued`, `running`, `done` or `dead`. `get(job_id)` returns a `Job`, raising `UnknownJob` for an unknown ID, `dead_letters()` lists the dead jobs, and `counts()` counts the jobs in each status.

## Leases

`claim(worker, *, lease_seconds=30.0)` leases the ready job with the earliest `run_at`, then the lowest ID, to `worker` and returns a `Lease`, or `None` when no job is ready. A claim counts one attempt and issues a token that identifies this lease alone. However many processes claim at once, a job has at most one valid lease at any moment.

A lease expires at `expires_at`, so it is valid only while the queue's clock reads less than that. Its holder calls `heartbeat(lease, *, lease_seconds=30.0)` to extend it, which returns the extended lease, and finishes the job with `complete(lease, result=None)` or `fail(lease, error)`. Each of these raises `LeaseLost` and changes nothing once the lease is no longer valid.

## Failures

`fail` records `error` as the job's `last_error`. A job with attempts left is queued again with `run_at` set `min(retry_delay * 2 ** (attempt - 1), max_retry_delay)` seconds after the failure, and a job that used its last attempt becomes dead.

A lease that expires counts as a failed attempt whose error is `lease expired`, with no delay before the job is ready again. When it was the job's last attempt, the job becomes dead instead.

## Workers

`Worker(queue, handlers, *, name, lease_seconds=30.0)` runs jobs by calling `handlers[kind](payload)`. A handler's return value completes the job, and an exception fails it. `run_once()` runs one ready job and returns whether there was one, and `run(until_idle=False)` keeps running jobs, returning how many ran once no job is ready when `until_idle` is set.

## Command line

```bash
python -m jobq DB enqueue KIND [PAYLOAD] [--dedupe-key KEY] [--delay SECONDS]
python -m jobq DB status JOB_ID
python -m jobq DB counts
python -m jobq DB dead
python -m jobq DB work MODULE --name NAME [--lease-seconds SECONDS] [--until-idle]
```

`PAYLOAD` is JSON. `enqueue` prints the job's ID, and `status`, `counts` and `dead` print JSON. `work` imports `MODULE`, runs jobs with its `HANDLERS` mapping, and prints how many ran.
