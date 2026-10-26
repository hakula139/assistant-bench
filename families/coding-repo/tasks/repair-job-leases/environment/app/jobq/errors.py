"""Errors the queue raises."""


class QueueError(Exception):
    """Base class for queue errors."""


class UnknownJob(QueueError):
    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        super().__init__(f'No job has ID {job_id}.')


class LeaseLost(QueueError):
    """A lease that expired or was superseded by a later claim."""

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        super().__init__(f'The lease on job {job_id} is no longer held.')
