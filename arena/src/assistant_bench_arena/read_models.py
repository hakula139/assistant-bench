"""Private scheduling inputs read from current ledger projections."""

from typing import Literal

from pydantic import NonNegativeInt

from assistant_bench_arena.models import Record


class FamilyState(Record):
    family: str
    evaluation_id: str
    publication_id: str
    point_id: str
    policy_id: str
    last_evidence_seq: NonNegativeInt
    revision: NonNegativeInt


class OwnedSession(Record):
    id: str
    actor_id: str
    family: str
    canonical_actor: str
    version: NonNegativeInt
    current_lease: str | None


class ClaimState(Record):
    id: str
    kind: Literal['ordinary', 'repeat']
    comparison_key: str
    repeat_of: NonNegativeInt | None
    version: NonNegativeInt
    selected_receipt: NonNegativeInt | None
    current_lease: str | None
    eligible: bool


class ClaimPage(Record):
    rows: tuple[ClaimState, ...]
    after: str | None
    """Continue after this key, or stop when absent."""


class Coverage(Record):
    a_system: str
    b_system: str
    count: NonNegativeInt


class CoveragePage(Record):
    rows: tuple[Coverage, ...]
    after: tuple[str, str] | None


class CasePage(Record):
    rows: tuple[str, ...]
    after: str | None


class RenderedContent(Record):
    render: Literal['text', 'html', 'diff']
    content: str


class DueRepeat(Record):
    receipt_seq: NonNegativeInt
    case_key: str
    a_output: str
    b_output: str
    claim_id: str | None
    claim_version: NonNegativeInt | None


class RepeatPage(Record):
    rows: tuple[DueRepeat, ...]
    after: NonNegativeInt | None
