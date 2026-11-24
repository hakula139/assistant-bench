"""Trial-scoped admission and durable usage receipts for model requests."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from assistant_bench.catalog import Api
from assistant_bench.harnesses.usage import Attempt, MeasuredUsage
from assistant_bench.state import AttemptStart, Claim, RequestAdmission, State


REQUEST_ID = 'bench_request_id'
_timestamp = TypeAdapter(datetime)


class AccountingError(ValueError):
    """Accounting could not authorize I/O or confirm its durable outcome."""


class RequestReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    api: Api
    outcome: Literal['completed', 'incomplete', 'failed', 'http_error', 'malformed']
    http_status: int
    usage: MeasuredUsage | None
    response_model: str | None = None
    response_id: str | None = None
    provider_request_id: str | None = None


class NotDispatched(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    api: Api
    outcome: Literal['not_dispatched'] = 'not_dispatched'
    reason: Literal['connect_timeout']


type Receipt = Annotated[RequestReceipt | NotDispatched, Field(discriminator='outcome')]
_receipt = TypeAdapter(Receipt)


def parse_receipt(value: str | None) -> Receipt | None:
    return _receipt.validate_json(value) if value is not None else None


def response_usage(receipts: Iterable[Receipt | None]) -> list[MeasuredUsage | None]:
    return [
        receipt.usage if receipt is not None else None
        for receipt in receipts
        if not isinstance(receipt, NotDispatched)
    ]


class TrialAccounting:
    """Keep a paid claim fenced until all admitted request outcomes are durable.

    Instances and remembered secrets remain in the trial's host process.
    The owning job plugin writes the final Entry and settles the claim.
    """

    def __init__(self, state: State, claim: Claim) -> None:
        self._state = state
        self._claim = claim
        self._attempt: AttemptStart | None = None
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._fatal = False
        self._requests: dict[str, Receipt | None] = {}
        self._apis: dict[str, Api] = {}
        self._secrets: set[str] = set()

    @property
    def claim_id(self) -> str:
        return self._claim.id

    @property
    def fatal(self) -> bool:
        return self._fatal

    def begin(self, started_at: datetime) -> None:
        if self._fatal or self._attempt is not None:
            self._fail('The paid attempt cannot start again.')
        self._started_at = started_at
        self._attempt = AttemptStart(
            id=str(uuid4()),
            claim_id=self.claim_id,
            started_at=_timestamp.dump_python(started_at, mode='json'),
        )
        try:
            started = self._state.begin_attempt(self._attempt)
        except Exception:
            self._fail('The paid attempt start could not be confirmed.')
        if not started:
            self._fail('The paid attempt start was not granted.')

    async def admit(self, api: Api) -> str:
        if self._fatal or self._attempt is None or self._finished_at is not None:
            self._fail('The paid attempt cannot admit another request.')
        request_id = str(uuid4())
        self._requests[request_id] = None
        self._apis[request_id] = api
        request = RequestAdmission(
            id=request_id,
            attempt_id=self._attempt.id,
            created_at=_timestamp.dump_python(datetime.now(UTC), mode='json'),
        )
        try:
            admitted = self._state.admit_request(request)
        except Exception:
            self._fail('The model request admission could not be confirmed.')
        if not admitted:
            del self._requests[request_id]
            del self._apis[request_id]
            raise AccountingError('The model request admission was not granted.')
        return request_id

    async def confirm(self, request_id: str, receipt: Receipt) -> None:
        if request_id not in self._requests or self._apis[request_id] != receipt.api:
            self._fail('The model receipt does not match an admitted request.')
        if isinstance(receipt, RequestReceipt):
            receipt = receipt.model_copy(
                update={
                    name: None
                    for name, value in (
                        ('response_id', receipt.response_id),
                        ('response_model', receipt.response_model),
                        ('provider_request_id', receipt.provider_request_id),
                    )
                    if value is not None
                    and any(secret in value for secret in self._secrets)
                }
            )
        previous = self._requests[request_id]
        if previous is not None and previous != receipt:
            self._fail('The model request already has a different receipt.')
        try:
            self._state.record_receipt(request_id, receipt.model_dump_json())
        except Exception:
            self._fail('The model response receipt could not be confirmed.')
        self._requests[request_id] = receipt

    async def uncertain(self) -> None:
        """Latch uncertainty even if the database cannot acknowledge it."""
        self._mark_uncertain()

    def finish(self, finished_at: datetime) -> None:
        self._finished_at = finished_at
        if any(receipt is None for receipt in self._requests.values()):
            self._mark_uncertain()

    @contextmanager
    def track(self) -> Generator[Self]:
        self.begin(datetime.now(UTC))
        try:
            yield self
        finally:
            self.finish(datetime.now(UTC))

    def attempt(self) -> Attempt | None:
        if self._started_at is None:
            return None
        if self._finished_at is None:
            self._fail('The paid attempt has not finished.')
        return Attempt(
            trial=self._claim.trial,
            system=self._claim.system,
            fingerprint=self._claim.fingerprint,
            started_at=self._started_at,
            finished_at=self._finished_at,
            responses=response_usage(self._requests.values()),
        )

    def remember_secret(self, value: str) -> None:
        if value:
            self._secrets.add(value)

    def secrets(self) -> tuple[str, ...]:
        return tuple(sorted(self._secrets))

    def _mark_uncertain(self) -> None:
        self._fatal = True
        with suppress(Exception):
            self._state.mark_uncertain(self.claim_id)

    def _fail(self, message: str) -> NoReturn:
        self._mark_uncertain()
        raise AccountingError(message) from None


_trial: ContextVar[TrialAccounting | None] = ContextVar('bench_trial', default=None)


def bind_trial(recorder: TrialAccounting) -> Token[TrialAccounting | None]:
    return _trial.set(recorder)


def reset_trial(token: Token[TrialAccounting | None]) -> None:
    _trial.reset(token)


def current_trial() -> TrialAccounting:
    recorder = _trial.get()
    if recorder is None:
        raise AccountingError('The model request has no bound paid trial.')
    return recorder
