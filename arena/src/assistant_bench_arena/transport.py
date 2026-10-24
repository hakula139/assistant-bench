"""Prepared transport boundary and enumerated domain rejections."""

from typing import Literal, Protocol

from assistant_bench_arena.ledger_models import Row, Statement


type Rejection = Literal[
    'attribution_context',
    'attribution_stale',
    'attribution_expiry',
    'candidate_stale',
    'claim_stale',
    'command_exists',
    'conflict',
    'credential_context',
    'defects_invalid',
    'family_missing',
    'family_stale',
    'immutable',
    'invalid_release',
    'lease_expiry',
    'lease_stale',
    'moderation_stale',
    'moderation_unsealed',
    'network_context',
    'pending_exists',
    'pending_limit',
    'point_downgrade',
    'private_material',
    'publication_unsealed',
    'proof_consumed',
    'quota_exhausted',
    'quota_policy',
    'repeat_ineligible',
    'session_stale',
]


class CommandRejected(Exception):
    """A transport maps only an exact, enumerated database guard to this error."""

    def __init__(self, reason: Rejection) -> None:
        self.reason = reason
        super().__init__(reason)


class Transport(Protocol):
    async def execute(self, statement: Statement) -> None:
        """Execute one statement atomically, preserving trigger aborts."""
        ...

    async def query(self, statement: Statement) -> list[Row]:
        """Return JSON-compatible rows without interpreting mutation metadata."""
        ...
