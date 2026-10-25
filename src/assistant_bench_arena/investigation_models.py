"""Private receipt investigation contracts and sampling provenance."""

from typing import Literal

from pydantic import NonNegativeInt

from assistant_bench_arena.ledger_models import Attribution, Identifier, Receipt
from assistant_bench_arena.models import FamilyName, Record
from assistant_bench_arena.read_models import FamilyState


class NetworkLookup(Record):
    version: Identifier
    kind: Literal['exact', 'prefix']
    tag: Identifier


class ReceiptFilter(Record):
    family: FamilyName
    actor_id: Identifier | None = None
    session_id: Identifier | None = None
    evaluation_id: Identifier | None = None

    recorded_from: NonNegativeInt | None = None
    recorded_before: NonNegativeInt | None = None
    status: Literal['any', 'active', 'withdrawn', 'excluded', 'superseded'] = 'any'
    network: NetworkLookup | None = None


class InvestigatedReceipt(Record):
    observation: Receipt
    claim_version: NonNegativeInt
    selected: bool
    eligible: bool
    withdrawn: NonNegativeInt | None
    excluded: NonNegativeInt | None
    superseded: NonNegativeInt | None
    network: tuple[Attribution, ...]
    """Associations still searchable at the trusted read clock."""


class InvestigationPage(Record):
    rows: tuple[InvestigatedReceipt, ...]
    after: NonNegativeInt | None


class SamplingSource(Record):
    export_id: str
    analysis_sequence: NonNegativeInt
    event_cutoff: NonNegativeInt
    receipt_cutoff: NonNegativeInt


class FamilyStatus(Record):
    state: FamilyState
    sampling_source: SamplingSource | None

    event_cutoff: NonNegativeInt
    receipt_cutoff: NonNegativeInt
    """Latest family-local immutable sequences at this read snapshot."""

    eligible_votes: NonNegativeInt
    """Raw selected ordinary coverage, before offline actor weighting."""
