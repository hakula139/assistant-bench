"""SQLAlchemy metadata declarations mirroring the vote-store and private SQL schemas."""

from sqlalchemy import Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata-only base for Core queries whose subclasses are never instantiated."""


# ── Vote Store ──


class Publications(Base):
    __tablename__ = 'publications'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    aspect: Mapped[str | None] = mapped_column(Text)
    defects: Mapped[str] = mapped_column(Text)
    reference: Mapped[str] = mapped_column(Text)
    systems: Mapped[str] = mapped_column(Text)
    case_count: Mapped[int] = mapped_column(Integer)
    output_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer)
    render: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)


class Cases(Base):
    __tablename__ = 'cases'

    publication_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_key: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text)
    instruction: Mapped[str] = mapped_column(Text)
    checklist: Mapped[str] = mapped_column(Text)
    split: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)


class Outputs(Base):
    __tablename__ = 'outputs'

    publication_id: Mapped[str] = mapped_column(Text, primary_key=True)
    output_key: Mapped[str] = mapped_column(Text, primary_key=True)
    case_key: Mapped[str] = mapped_column(Text)
    system: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(Text)
    errored: Mapped[int] = mapped_column(Integer)
    content_digest: Mapped[str] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    length: Mapped[int | None] = mapped_column(Integer)


class Active(Base):
    __tablename__ = 'active'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    publication_id: Mapped[str] = mapped_column(Text)
    activated_at: Mapped[int] = mapped_column(Integer)


class Actors(Base):
    __tablename__ = 'actors'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[int] = mapped_column(Integer)
    ip_tag: Mapped[str | None] = mapped_column(Text)


class Pairs(Base):
    __tablename__ = 'pairs'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text)
    family: Mapped[str] = mapped_column(Text)
    publication_id: Mapped[str] = mapped_column(Text)
    case_key: Mapped[str] = mapped_column(Text)
    a_output: Mapped[str] = mapped_column(Text)
    b_output: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[int] = mapped_column(Integer)


class Votes(Base):
    __tablename__ = 'votes'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(Text)
    family: Mapped[str] = mapped_column(Text)
    publication_id: Mapped[str] = mapped_column(Text)
    case_key: Mapped[str] = mapped_column(Text)
    a_output: Mapped[str] = mapped_column(Text)
    b_output: Mapped[str] = mapped_column(Text)
    a_system: Mapped[str] = mapped_column(Text)
    b_system: Mapped[str] = mapped_column(Text)
    choice: Mapped[str] = mapped_column(Text)
    defects: Mapped[str] = mapped_column(Text)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[int] = mapped_column(Integer)
    ip_tag: Mapped[str | None] = mapped_column(Text)
    exclusion: Mapped[str | None] = mapped_column(Text)


class PairVoteCounts(Base):
    __tablename__ = 'pair_vote_counts'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    low_system: Mapped[str] = mapped_column(Text, primary_key=True)
    high_system: Mapped[str] = mapped_column(Text, primary_key=True)
    votes: Mapped[int] = mapped_column(Integer)


class PublicationPairCases(Base):
    __tablename__ = 'publication_pair_cases'

    publication_id: Mapped[str] = mapped_column(Text, primary_key=True)
    low_system: Mapped[str] = mapped_column(Text, primary_key=True)
    high_system: Mapped[str] = mapped_column(Text, primary_key=True)
    case_keys: Mapped[str] = mapped_column(Text)


class BatchPairs(Base):
    __tablename__ = 'batch_pairs'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text)
    family: Mapped[str] = mapped_column(Text)
    publication_id: Mapped[str] = mapped_column(Text)
    case_key: Mapped[str] = mapped_column(Text)
    a_output: Mapped[str] = mapped_column(Text)
    b_output: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)


# ── Private State ──


class SourceHeads(Base):
    __tablename__ = 'source_heads'

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)


class Runs(Base):
    __tablename__ = 'runs'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text)
    job: Mapped[str] = mapped_column(Text)
    code_revision: Mapped[str | None] = mapped_column(Text)
    case_revision: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    max_requests: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)


class AttemptClaims(Base):
    __tablename__ = 'attempt_claims'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    slot_key: Mapped[str] = mapped_column(Text)
    trial: Mapped[str] = mapped_column(Text)
    system: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    accounting_json: Mapped[str] = mapped_column(Text)
    expected_source_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)


class AttemptStarts(Base):
    __tablename__ = 'attempt_starts'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    claim_id: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text)


class AttemptEntries(Base):
    __tablename__ = 'attempt_entries'

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    job: Mapped[str] = mapped_column(Text, primary_key=True)
    trial: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[str] = mapped_column(Text, primary_key=True)
    claim_id: Mapped[str | None] = mapped_column(Text)
    attempt_id: Mapped[str | None] = mapped_column(Text)
    entry_json: Mapped[str] = mapped_column(Text)


class RequestAdmissions(Base):
    __tablename__ = 'request_admissions'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class RequestReceipts(Base):
    __tablename__ = 'request_receipts'

    request_id: Mapped[str] = mapped_column(Text, primary_key=True)
    receipt_json: Mapped[str] = mapped_column(Text)


class TrialArtifacts(Base):
    __tablename__ = 'trial_artifacts'

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trial_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_trial_id: Mapped[str] = mapped_column(Text)
    commit_sha: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    manifest_digest: Mapped[str] = mapped_column(Text)


class ClaimArtifacts(Base):
    __tablename__ = 'claim_artifacts'

    claim_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text)
    trial_id: Mapped[str] = mapped_column(Text)


class Collections(Base):
    __tablename__ = 'collections'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text)
    expected_count: Mapped[int] = mapped_column(Integer)
    manifest_digest: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    code_revision: Mapped[str | None] = mapped_column(Text)
    case_revision: Mapped[str | None] = mapped_column(Text)
    system_names_json: Mapped[str | None] = mapped_column(Text)
    sealed: Mapped[int] = mapped_column(Integer)


class CollectionOutputs(Base):
    __tablename__ = 'collection_outputs'

    collection_id: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    output_key: Mapped[str] = mapped_column(Text)
    output_json: Mapped[str] = mapped_column(Text)


class ActiveCollections(Base):
    __tablename__ = 'active_collections'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    collection_id: Mapped[str] = mapped_column(Text)


class CanaryRecords(Base):
    __tablename__ = 'canary_records'

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    system: Mapped[str] = mapped_column(Text)
    record_json: Mapped[str] = mapped_column(Text)


class CanaryBaselines(Base):
    __tablename__ = 'canary_baselines'

    system: Mapped[str] = mapped_column(Text, primary_key=True)
    context_key: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text)


class PublicationPreparations(Base):
    __tablename__ = 'publication_preparations'

    publication_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text)
    collection_id: Mapped[str] = mapped_column(Text)
    case_revision: Mapped[str | None] = mapped_column(Text)
    prepared_at: Mapped[str] = mapped_column(Text)


class VoteSnapshots(Base):
    __tablename__ = 'vote_snapshots'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text)
    taken_at: Mapped[str] = mapped_column(Text)
    closed_at: Mapped[str] = mapped_column(Text)
    category_order_json: Mapped[str] = mapped_column(Text)
    closing_revision: Mapped[str | None] = mapped_column(Text)
    expected_store_count: Mapped[int] = mapped_column(Integer)
    expected_case_count: Mapped[int] = mapped_column(Integer)
    expected_output_count: Mapped[int] = mapped_column(Integer)
    expected_vote_count: Mapped[int] = mapped_column(Integer)
    manifest_digest: Mapped[str] = mapped_column(Text)
    sealed: Mapped[int] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(Text)


class VoteSnapshotStores(Base):
    __tablename__ = 'vote_snapshot_stores'

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    publication_id: Mapped[str] = mapped_column(Text)
    reference: Mapped[str] = mapped_column(Text)
    systems_json: Mapped[str] = mapped_column(Text)
    defects_json: Mapped[str] = mapped_column(Text)
    cutoff: Mapped[int] = mapped_column(Integer)
    excluded: Mapped[int] = mapped_column(Integer)
    output_generation_id: Mapped[str | None] = mapped_column(Text)


class VoteSnapshotCases(Base):
    __tablename__ = 'vote_snapshot_cases'

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    case_key: Mapped[str] = mapped_column(Text, primary_key=True)
    facts_json: Mapped[str] = mapped_column(Text)


class VoteSnapshotOutputs(Base):
    __tablename__ = 'vote_snapshot_outputs'

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    output_key: Mapped[str] = mapped_column(Text, primary_key=True)
    facts_json: Mapped[str] = mapped_column(Text)


class VoteSnapshotVotes(Base):
    __tablename__ = 'vote_snapshot_votes'

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    vote_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vote_json: Mapped[str] = mapped_column(Text)


class PendingTrialArtifacts(Base):
    __tablename__ = 'pending_trial_artifacts'

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trial_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_trial_id: Mapped[str] = mapped_column(Text)
    staged_commit_sha: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    manifest_digest: Mapped[str] = mapped_column(Text)
    authorized_claim_id: Mapped[str | None] = mapped_column(Text)
    publication_ready: Mapped[int] = mapped_column(Integer)
    canonical_commit_sha: Mapped[str | None] = mapped_column(Text)


class LeaderboardCandidates(Base):
    __tablename__ = 'leaderboard_candidates'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    hour_slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    captured_at: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(Text)
    cutoffs_json: Mapped[str | None] = mapped_column(Text)
    report_json: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[int | None] = mapped_column(Integer)
