"""Strict browser commands and safe acknowledgments for the voting runtime."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, NonNegativeInt

from assistant_bench_arena.models import (
    Category,
    Choice,
    Defects,
    FamilyName,
    FamilyView,
    Pane,
    Record,
)
from assistant_bench_arena.runtime_models import Identifier


type Capability = Annotated[str, Field(min_length=1, max_length=2048)]
type ChallengeProof = Annotated[str, Field(min_length=1, max_length=2048)]


class Command(Record):
    command_id: UUID = Field(strict=False)
    """Stable across retries of this exact submitted meaning."""


class AdmissionRequest(Command):
    proof: ChallengeProof


class AdmissionReplay(Command):
    proof: ChallengeProof | None = None
    """Initial admission requires the original proof. Renewal can use its old cookie."""


class StatusView(Record):
    status: Literal['admission_required', 'renewal_required', 'ready']


class AdmissionView(Command):
    action: Literal['bench_admit', 'bench_renew']
    sitekey: Identifier


class PairView(Record):
    id: Identifier
    """Non-secret lease locator for exact receipt recovery."""

    capability: Capability
    instruction: str
    checklist: list[str]
    category: Category | None
    """The case's category ID, whose label the site's glossary supplies."""

    a: Pane
    b: Pane


class SessionView(Record):
    id: Identifier
    family: FamilyView
    votes: NonNegativeInt
    """Current selected choices from this session."""

    remaining: NonNegativeInt
    """Drawable cases, including the current pending pair's case."""

    undo_acknowledgment: Identifier | None
    """Latest receipt eligible for explicit withdrawal, safe to retain as a locator."""

    pair: PairView | None


type ErrorCode = Literal[
    'invalid_request',
    'admission_required',
    'renewal_required',
    'challenge_rejected',
    'rate_limited',
    'not_found',
    'conflict',
    'unavailable',
]


class ErrorView(Record):
    error: ErrorCode


class StartSession(Command):
    family: FamilyName


class ResumeSession(Command):
    session_id: Identifier


class ReceiptQuery(Command):
    lease_id: Identifier
    """Non-secret pair locator, safe to retain with an uncertain draft."""

    choice: Choice
    defects: Defects = Defects()


class VoteRequest(ReceiptQuery):
    capability: Capability
    """Kept only in document memory and the submitted POST body."""


class VoteAcknowledgment(Record):
    acknowledgment: Identifier
    session_id: Identifier


class WithdrawRequest(Command):
    session_id: Identifier
    acknowledgment: Identifier
    """The exact receipt the visitor intends to withdraw."""


class FreshnessRequest(Record):
    export_id: Identifier


class FreshnessView(Record):
    status: Literal['current', 'stale', 'unknown']
