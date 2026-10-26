"""A worker loop that runs claimed jobs through registered handlers."""

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from .errors import LeaseLost
from .queue import Queue


type Handler = Callable[[Any], Any]

log = logging.getLogger('jobq')


class Worker:
    def __init__(
        self,
        queue: Queue,
        handlers: Mapping[str, Handler],
        *,
        name: str,
        lease_seconds: float = 30.0,
        idle_sleep: float = 0.5,
    ) -> None:
        self.queue = queue
        self.handlers = handlers
        self.name = name
        self.lease_seconds = lease_seconds
        self.idle_sleep = idle_sleep

    def run_once(self) -> bool:
        """Run one ready job, returning whether there was one."""
        lease = self.queue.claim(self.name, lease_seconds=self.lease_seconds)
        if lease is None:
            return False

        handler = self.handlers.get(lease.kind)
        try:
            if handler is None:
                self.queue.fail(lease, f'No handler for {lease.kind!r}.')
                return True

            try:
                result = handler(lease.payload)
            except Exception as error:
                log.warning('job %d failed: %r', lease.job_id, error)
                self.queue.fail(lease, f'{type(error).__name__}: {error}')
            else:
                self.queue.complete(lease, result)
        except LeaseLost:
            log.warning('job %d outlived its lease', lease.job_id)
        return True

    def run(self, *, until_idle: bool = False) -> int:
        """Run jobs until stopped, or until none is ready, and return how many ran."""
        processed = 0
        while True:
            if self.run_once():
                processed += 1
            elif until_idle:
                return processed
            else:
                time.sleep(self.idle_sleep)
