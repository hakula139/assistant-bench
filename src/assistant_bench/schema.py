"""SQLAlchemy metadata declarations mirroring the vote-store and private SQL schemas."""

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata-only base for Core queries; classes are never instantiated."""


# ── Vote Store ──


class Publications(Base):
    __tablename__ = 'publications'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    aspect: Mapped[str | None] = mapped_column(Text, nullable=True)
    defects: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    systems: Mapped[str] = mapped_column(Text, nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    output_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    render: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)


class Cases(Base):
    __tablename__ = 'cases'

    publication_id: Mapped[str] = mapped_column(
        Text, ForeignKey('publications.id'), primary_key=True
    )
    case_key: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    checklist: Mapped[str] = mapped_column(Text, nullable=False)
    split: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)


class Outputs(Base):
    __tablename__ = 'outputs'

    publication_id: Mapped[str] = mapped_column(Text, primary_key=True)
    output_key: Mapped[str] = mapped_column(Text, primary_key=True)
    case_key: Mapped[str] = mapped_column(Text, nullable=False)
    system: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    errored: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    length: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Active(Base):
    __tablename__ = 'active'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    publication_id: Mapped[str] = mapped_column(
        Text, ForeignKey('publications.id'), nullable=False
    )
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Actors(Base):
    __tablename__ = 'actors'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_tag: Mapped[str | None] = mapped_column(Text, nullable=True)


class Pairs(Base):
    __tablename__ = 'pairs'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text, ForeignKey('actors.id'), nullable=False)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    publication_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_key: Mapped[str] = mapped_column(Text, nullable=False)
    a_output: Mapped[str] = mapped_column(Text, nullable=False)
    b_output: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Votes(Base):
    __tablename__ = 'votes'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pair_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(Text, ForeignKey('actors.id'), nullable=False)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    publication_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_key: Mapped[str] = mapped_column(Text, nullable=False)
    a_output: Mapped[str] = mapped_column(Text, nullable=False)
    b_output: Mapped[str] = mapped_column(Text, nullable=False)
    a_system: Mapped[str] = mapped_column(Text, nullable=False)
    b_system: Mapped[str] = mapped_column(Text, nullable=False)
    choice: Mapped[str] = mapped_column(Text, nullable=False)
    defects: Mapped[str] = mapped_column(Text, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclusion: Mapped[str | None] = mapped_column(Text, nullable=True)


class PairVoteCounts(Base):
    __tablename__ = 'pair_vote_counts'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    low_system: Mapped[str] = mapped_column(Text, primary_key=True)
    high_system: Mapped[str] = mapped_column(Text, primary_key=True)
    votes: Mapped[int] = mapped_column(Integer, nullable=False)


class PublicationPairCases(Base):
    __tablename__ = 'publication_pair_cases'

    publication_id: Mapped[str] = mapped_column(
        Text, ForeignKey('publications.id'), primary_key=True
    )
    low_system: Mapped[str] = mapped_column(Text, primary_key=True)
    high_system: Mapped[str] = mapped_column(Text, primary_key=True)
    case_keys: Mapped[str] = mapped_column(Text, nullable=False)


class BatchPairs(Base):
    __tablename__ = 'batch_pairs'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text, ForeignKey('actors.id'), nullable=False)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    publication_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_key: Mapped[str] = mapped_column(Text, nullable=False)
    a_output: Mapped[str] = mapped_column(Text, nullable=False)
    b_output: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


# ── Private State ──


class SourceHeads(Base):
    __tablename__ = 'source_heads'

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class Runs(Base):
    __tablename__ = 'runs'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    job: Mapped[str] = mapped_column(Text, nullable=False)
    code_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    max_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)


class AttemptClaims(Base):
    __tablename__ = 'attempt_claims'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey('runs.id'), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    slot_key: Mapped[str] = mapped_column(Text, nullable=False)
    trial: Mapped[str] = mapped_column(Text, nullable=False)
    system: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    accounting_json: Mapped[str] = mapped_column(Text, nullable=False)
    expected_source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)


class AttemptStarts(Base):
    __tablename__ = 'attempt_starts'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        Text, ForeignKey('attempt_claims.id'), nullable=False
    )
    started_at: Mapped[str] = mapped_column(Text, nullable=False)


class AttemptEntries(Base):
    __tablename__ = 'attempt_entries'

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    job: Mapped[str] = mapped_column(Text, primary_key=True)
    trial: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[str] = mapped_column(Text, primary_key=True)
    claim_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey('attempt_claims.id'), nullable=True
    )
    attempt_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey('attempt_starts.id'), nullable=True, unique=True
    )
    entry_json: Mapped[str] = mapped_column(Text, nullable=False)


class RequestAdmissions(Base):
    __tablename__ = 'request_admissions'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        Text, ForeignKey('attempt_starts.id'), nullable=False
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class RequestReceipts(Base):
    __tablename__ = 'request_receipts'

    request_id: Mapped[str] = mapped_column(
        Text, ForeignKey('request_admissions.id'), primary_key=True
    )
    receipt_json: Mapped[str] = mapped_column(Text, nullable=False)


class TrialArtifacts(Base):
    __tablename__ = 'trial_artifacts'

    run_id: Mapped[str] = mapped_column(Text, ForeignKey('runs.id'), primary_key=True)
    trial_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_trial_id: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(Text, nullable=False)


class ClaimArtifacts(Base):
    __tablename__ = 'claim_artifacts'

    claim_id: Mapped[str] = mapped_column(
        Text, ForeignKey('attempt_claims.id'), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    trial_id: Mapped[str] = mapped_column(Text, nullable=False)


class Collections(Base):
    __tablename__ = 'collections'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    code_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_names_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sealed: Mapped[int] = mapped_column(Integer, nullable=False)


class CollectionOutputs(Base):
    __tablename__ = 'collection_outputs'

    collection_id: Mapped[str] = mapped_column(
        Text, ForeignKey('collections.id'), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    output_key: Mapped[str] = mapped_column(Text, nullable=False)
    output_json: Mapped[str] = mapped_column(Text, nullable=False)


class ActiveCollections(Base):
    __tablename__ = 'active_collections'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    collection_id: Mapped[str] = mapped_column(Text, nullable=False)


class CanaryRecords(Base):
    __tablename__ = 'canary_records'

    run_id: Mapped[str] = mapped_column(Text, ForeignKey('runs.id'), primary_key=True)
    system: Mapped[str] = mapped_column(Text, nullable=False)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)


class CanaryBaselines(Base):
    __tablename__ = 'canary_baselines'

    system: Mapped[str] = mapped_column(Text, primary_key=True)
    context_key: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey('canary_records.run_id'), nullable=False
    )


class PublicationPreparations(Base):
    __tablename__ = 'publication_preparations'

    publication_id: Mapped[str] = mapped_column(
        Text, ForeignKey('publications.id'), primary_key=True
    )
    target: Mapped[str] = mapped_column(Text, nullable=False)
    collection_id: Mapped[str] = mapped_column(
        Text, ForeignKey('collections.id'), nullable=False
    )
    case_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    prepared_at: Mapped[str] = mapped_column(Text, nullable=False)


class VoteSnapshots(Base):
    __tablename__ = 'vote_snapshots'

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    taken_at: Mapped[str] = mapped_column(Text, nullable=False)
    closed_at: Mapped[str] = mapped_column(Text, nullable=False)
    category_order_json: Mapped[str] = mapped_column(Text, nullable=False)
    closing_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_store_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_output_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_vote_count: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(Text, nullable=False)
    sealed: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str | None] = mapped_column(Text, nullable=True)


class VoteSnapshotStores(Base):
    __tablename__ = 'vote_snapshot_stores'

    snapshot_id: Mapped[str] = mapped_column(
        Text, ForeignKey('vote_snapshots.id'), primary_key=True
    )
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    publication_id: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    systems_json: Mapped[str] = mapped_column(Text, nullable=False)
    defects_json: Mapped[str] = mapped_column(Text, nullable=False)
    cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded: Mapped[int] = mapped_column(Integer, nullable=False)
    output_generation_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey('collections.id'), nullable=True
    )


class VoteSnapshotCases(Base):
    __tablename__ = 'vote_snapshot_cases'

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    case_key: Mapped[str] = mapped_column(Text, primary_key=True)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)


class VoteSnapshotOutputs(Base):
    __tablename__ = 'vote_snapshot_outputs'

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    output_key: Mapped[str] = mapped_column(Text, primary_key=True)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)


class VoteSnapshotVotes(Base):
    __tablename__ = 'vote_snapshot_votes'

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    vote_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vote_json: Mapped[str] = mapped_column(Text, nullable=False)


class PendingTrialArtifacts(Base):
    __tablename__ = 'pending_trial_artifacts'

    run_id: Mapped[str] = mapped_column(Text, ForeignKey('runs.id'), primary_key=True)
    trial_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_trial_id: Mapped[str] = mapped_column(Text, nullable=False)
    staged_commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_claim_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey('attempt_claims.id'), nullable=True, unique=True
    )
    publication_ready: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)


class LeaderboardCandidates(Base):
    __tablename__ = 'leaderboard_candidates'

    family: Mapped[str] = mapped_column(Text, primary_key=True)
    hour_slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    captured_at: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    cutoffs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
