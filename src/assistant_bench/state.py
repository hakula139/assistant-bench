"""Private run state and immutable records shared by disposable clients."""

import hashlib
import json
from collections.abc import Sequence
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import (
    Integer,
    Text,
    and_,
    column,
    delete,
    exists,
    func,
    literal,
    literal_column,
    null,
    or_,
    select,
    union_all,
    update,
    values,
)
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import aliased

from assistant_bench import schema
from assistant_bench.database import (
    Database,
    Row,
    execute,
    fetch_all,
    fetch_one,
    fetch_scalar,
)
from assistant_bench.messages import InputError


type ClaimState = Literal['claimed', 'uncertain', 'settled', 'cancelled']
type RunStatus = Literal['prepared', 'running', 'complete', 'uncertain']

OUTPUTS_PER_STATEMENT = 25
"""Four parameters per output fit D1's 100-bound-parameter limit."""


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Run(_Model):
    """Revision fields are None only for unknown historical execution provenance."""

    id: str
    scope: str
    job: str
    code_revision: str | None
    case_revision: str | None
    created_at: str
    max_requests: int | None = None


class Claim(_Model):
    id: str
    run_id: str
    scope: str
    slot_key: str
    trial: str
    system: str
    fingerprint: str
    accounting_json: str
    expected_source_revision: int
    created_at: str


class AttemptStart(_Model):
    """Use the owning Entry's canonical JSON timestamp for started_at."""

    id: str
    claim_id: str
    started_at: str


class RequestAdmission(_Model):
    id: str
    attempt_id: str
    created_at: str


class Collection(_Model):
    id: str
    family: str
    expected_count: int
    manifest_digest: str
    created_at: str
    code_revision: str | None
    case_revision: str | None
    system_names_json: str | None


class OutputGeneration(_Model):
    id: str | None
    outputs: tuple[str, ...]


class TrialArtifact(_Model):
    run_id: str
    trial_id: str
    source_trial_id: str
    commit_sha: str
    path: str
    manifest_digest: str


class PendingArtifact(_Model):
    run_id: str
    trial_id: str
    source_trial_id: str
    staged_commit_sha: str
    path: str
    manifest_digest: str
    authorized_claim_id: str | None
    publication_ready: bool
    canonical_commit_sha: str | None


class ArtifactGeneration(_Model):
    revision: int
    artifacts: tuple[TrialArtifact, ...]


class _StoredRun(Run):
    status: RunStatus


class _StoredClaim(Claim):
    state: ClaimState


class _StoredCollection(Collection):
    sealed: bool


class _StagedOutput(_Model):
    position: int
    output_key: str
    output_json: str


class _PositionOutput(_Model):
    position: int
    output_json: str


class _EntryMatch(_Model):
    claim_id: str | None
    entry_json: str


class _GenerationRow(_Model):
    collection_id: str
    output_json: str | None


class _ArtifactGenerationRow(_Model):
    revision: int
    run_id: str | None
    trial_id: str | None
    source_trial_id: str | None
    commit_sha: str | None
    path: str | None
    manifest_digest: str | None


class State:
    """Conditional claims and immutable domain-validated JSON records.

    JSON inputs come from their owning models. This layer preserves their bytes
    and frozen accounting without calculating prices or interpreting outcomes.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    # ── Runs and Paid Claims ──

    def create_run(self, run: Run) -> None:
        runs = schema.Runs
        execute(
            self._database,
            sqlite.insert(runs)
            .values(
                id=run.id,
                scope=run.scope,
                job=run.job,
                code_revision=run.code_revision,
                case_revision=run.case_revision,
                created_at=run.created_at,
                max_requests=run.max_requests,
            )
            .on_conflict_do_nothing(index_elements=[runs.id]),
        )
        if self._run(run.id) != run:
            raise InputError(f'Run `{run.id}` already has different metadata.')

    def source_revision(self, scope: str) -> int:
        revision = fetch_scalar(
            self._database,
            select(schema.SourceHeads.revision).where(
                schema.SourceHeads.scope == scope
            ),
            int,
        )
        return revision if revision is not None else 0

    def claim_attempt(self, claim: Claim) -> bool:
        """Claim a slot only at the source revision used to plan its generation.

        A repeated claim ID returns false, so it never admits another paid call.
        Claimed and uncertain slots remain occupied until settlement or
        cancellation before an attempt starts.
        """
        if _object(claim.accounting_json)['system'] != claim.system:
            raise InputError(f'Claim `{claim.id}` has a different accounting system.')
        claims = schema.AttemptClaims
        runs = schema.Runs
        source_heads = schema.SourceHeads
        rows = execute(
            self._database,
            sqlite.insert(claims)
            .from_select(
                [
                    claims.id,
                    claims.run_id,
                    claims.scope,
                    claims.slot_key,
                    claims.trial,
                    claims.system,
                    claims.fingerprint,
                    claims.accounting_json,
                    claims.expected_source_revision,
                    claims.created_at,
                ],
                select(
                    literal(claim.id),
                    literal(claim.run_id),
                    literal(claim.scope),
                    literal(claim.slot_key),
                    literal(claim.trial),
                    literal(claim.system),
                    literal(claim.fingerprint),
                    literal(claim.accounting_json),
                    literal(claim.expected_source_revision),
                    literal(claim.created_at),
                ).where(
                    exists(
                        select(literal(1)).where(
                            runs.id == claim.run_id,
                            runs.scope == claim.scope,
                            runs.status.in_(['prepared', 'running']),
                        )
                    ),
                    func.coalesce(
                        select(source_heads.revision)
                        .where(source_heads.scope == claim.scope)
                        .scalar_subquery(),
                        0,
                    )
                    == claim.expected_source_revision,
                ),
            )
            .on_conflict_do_nothing()
            .returning(claims.id),
        ).rows
        if rows:
            return True

        previous = fetch_one(
            self._database,
            select(
                claims.id,
                claims.run_id,
                claims.scope,
                claims.slot_key,
                claims.trial,
                claims.system,
                claims.fingerprint,
                claims.accounting_json,
                claims.expected_source_revision,
                claims.created_at,
            ).where(claims.id == claim.id),
            Claim,
        )
        if previous is not None and (
            previous.model_copy(update={'accounting_json': claim.accounting_json})
            != claim
            or not _same_json(previous.accounting_json, claim.accounting_json)
        ):
            raise InputError(f'Claim `{claim.id}` already has different metadata.')
        return False

    def get_claim(self, claim_id: str) -> Claim:
        stored = self._stored_claim(claim_id)
        return Claim.model_validate(stored.model_dump(exclude={'state'}))

    def claim_state(self, claim_id: str) -> ClaimState:
        return self._stored_claim(claim_id).state

    def mark_uncertain(self, claim_id: str) -> None:
        self._stored_claim(claim_id)
        claims = schema.AttemptClaims
        execute(
            self._database,
            update(claims)
            .values(state='uncertain')
            .where(claims.id == claim_id, claims.state == 'claimed'),
        )

    def cancel_claim(self, claim_id: str) -> bool:
        """Close a claim only when no attempt has crossed its start fence."""
        self._stored_claim(claim_id)
        claims = schema.AttemptClaims
        starts = schema.AttemptStarts
        return bool(
            execute(
                self._database,
                update(claims)
                .values(state='cancelled')
                .where(
                    claims.id == claim_id,
                    claims.state.in_(['claimed', 'uncertain']),
                    ~exists(select(literal(1)).where(starts.claim_id == claims.id)),
                )
                .returning(claims.id),
            ).rows
        )

    def cancel_unstarted(self, run_id: str) -> int:
        """Release only this run's claimed slots that never crossed a start fence."""
        claims = schema.AttemptClaims
        starts = schema.AttemptStarts
        return len(
            execute(
                self._database,
                update(claims)
                .values(state='cancelled')
                .where(
                    claims.run_id == run_id,
                    claims.state == 'claimed',
                    ~exists(select(literal(1)).where(starts.claim_id == claims.id)),
                )
                .returning(claims.id),
            ).rows
        )

    def begin_attempt(self, attempt: AttemptStart) -> bool:
        """Persist the mandatory start fence before any paid request.

        Only a true result grants a new start. Repeated identities return false
        and cannot authorize another attempt or another paid request.
        """
        self._stored_claim(attempt.claim_id)
        starts = schema.AttemptStarts
        claims = schema.AttemptClaims
        rows = execute(
            self._database,
            sqlite.insert(starts)
            .from_select(
                [starts.id, starts.claim_id, starts.started_at],
                select(
                    literal(attempt.id),
                    claims.id,
                    literal(attempt.started_at),
                ).where(claims.id == attempt.claim_id, claims.state == 'claimed'),
            )
            .on_conflict_do_nothing()
            .returning(starts.id),
        ).rows
        if rows:
            return True
        previous = fetch_one(
            self._database,
            select(starts.id, starts.claim_id, starts.started_at).where(
                starts.id == attempt.id
            ),
            AttemptStart,
        )
        if previous is not None and previous != attempt:
            raise InputError(f'Attempt `{attempt.id}` already has different metadata.')
        return False

    def admit_request(self, request: RequestAdmission) -> bool:
        """Grant one paid request ID under a live, unfinished attempt.

        False never permits I/O, including after an uncertain response to the
        admission itself. A request ID is never reused to resend a paid request.
        """
        requests = schema.RequestAdmissions
        starts = aliased(schema.AttemptStarts, name='starts')
        claims = aliased(schema.AttemptClaims, name='claims')
        runs = schema.Runs
        admissions = aliased(schema.RequestAdmissions, name='admissions')
        admitted_starts = aliased(schema.AttemptStarts, name='admitted_starts')
        admitted_claims = aliased(schema.AttemptClaims, name='admitted_claims')
        entries = schema.AttemptEntries
        admitted_count = (
            select(func.count())
            .select_from(admissions)
            .join(admitted_starts, admitted_starts.id == admissions.attempt_id)
            .join(admitted_claims, admitted_claims.id == admitted_starts.claim_id)
            .where(admitted_claims.run_id == runs.id)
            .correlate(runs)
            .scalar_subquery()
        )
        rows = execute(
            self._database,
            sqlite.insert(requests)
            .from_select(
                [requests.id, requests.attempt_id, requests.created_at],
                select(
                    literal(request.id),
                    starts.id,
                    literal(request.created_at),
                )
                .select_from(starts)
                .join(claims, claims.id == starts.claim_id)
                .join(runs, runs.id == claims.run_id)
                .where(
                    starts.id == request.attempt_id,
                    claims.state == 'claimed',
                    or_(
                        runs.max_requests.is_(None),
                        admitted_count < runs.max_requests,
                    ),
                    ~exists(select(literal(1)).where(entries.attempt_id == starts.id)),
                ),
            )
            .on_conflict_do_nothing(index_elements=[requests.id])
            .returning(requests.id),
        ).rows
        if rows:
            return True
        previous = fetch_one(
            self._database,
            select(requests.id, requests.attempt_id, requests.created_at).where(
                requests.id == request.id
            ),
            RequestAdmission,
        )
        if previous is not None and previous != request:
            raise InputError(f'Request `{request.id}` already has different metadata.')
        return False

    def record_receipt(self, request_id: str, receipt_json: str) -> None:
        """Append a domain-validated terminal receipt, preserving unknown usage."""
        receipts = schema.RequestReceipts
        execute(
            self._database,
            sqlite.insert(receipts)
            .values(request_id=request_id, receipt_json=receipt_json)
            .on_conflict_do_nothing(index_elements=[receipts.request_id]),
        )
        stored = fetch_scalar(
            self._database,
            select(receipts.receipt_json).where(receipts.request_id == request_id),
            str,
        )
        if stored is None or not _same_json(stored, receipt_json):
            raise InputError(f'Request `{request_id}` already has a different receipt.')

    def requests(self, scope: str, *, unrecorded_only: bool = False) -> list[Row]:
        """Read admitted requests with frozen accounting and terminal receipts.

        A missing receipt is None and means unknown usage.
        Unrecorded requests exclude every attempt covered by a final Entry.
        """
        requests = aliased(schema.RequestAdmissions, name='requests')
        receipts = aliased(schema.RequestReceipts, name='receipts')
        starts = aliased(schema.AttemptStarts, name='starts')
        claims = aliased(schema.AttemptClaims, name='claims')
        entries = schema.AttemptEntries
        stmt = (
            select(
                requests.id.label('request_id'),
                requests.attempt_id,
                starts.claim_id,
                requests.created_at,
                claims.accounting_json,
                receipts.receipt_json,
            )
            .select_from(requests)
            .outerjoin(receipts, receipts.request_id == requests.id)
            .join(starts, starts.id == requests.attempt_id)
            .join(claims, claims.id == starts.claim_id)
            .where(claims.scope == scope)
            .order_by(requests.created_at, requests.id)
        )
        if unrecorded_only:
            stmt = stmt.where(
                ~exists(select(literal(1)).where(entries.attempt_id == starts.id))
            )
        return execute(self._database, stmt).rows

    def trial_requests(self, run_id: str, trial_id: str) -> list[Row]:
        """Read canonical receipts for admissions belonging to one exact trial."""
        requests = aliased(schema.RequestAdmissions, name='requests')
        starts = aliased(schema.AttemptStarts, name='starts')
        claims = aliased(schema.AttemptClaims, name='claims')
        receipts = aliased(schema.RequestReceipts, name='receipts')
        return execute(
            self._database,
            select(requests.id.label('request_id'), receipts.receipt_json)
            .select_from(requests)
            .join(starts, starts.id == requests.attempt_id)
            .join(claims, claims.id == starts.claim_id)
            .outerjoin(receipts, receipts.request_id == requests.id)
            .where(claims.run_id == run_id, claims.trial == trial_id)
            .order_by(requests.created_at, requests.id),
        ).rows

    def record_entry(self, claim_id: str, entry_json: str) -> None:
        """Append one already-priced Entry without releasing its paid slot."""
        claim = self.get_claim(claim_id)
        run = self._run(claim.run_id)
        entry = _object(entry_json)
        accounting = _object(claim.accounting_json)
        expected = {
            'job': run.job,
            'trial': claim.trial,
            'system': claim.system,
            'fingerprint': claim.fingerprint,
            'price': accounting['price'],
            'ceiling_usd': accounting['ceiling_usd'],
        }
        if any(entry[name] != value for name, value in expected.items()):
            raise InputError(
                f'Entry for claim `{claim_id}` differs from its frozen '
                'identity or accounting.'
            )

        started_at = cast(str, entry['started_at'])
        entries = schema.AttemptEntries
        claims = aliased(schema.AttemptClaims, name='claims')
        starts = aliased(schema.AttemptStarts, name='starts')
        runs = schema.Runs
        execute(
            self._database,
            sqlite.insert(entries)
            .from_select(
                [
                    entries.scope,
                    entries.job,
                    entries.trial,
                    entries.started_at,
                    entries.claim_id,
                    entries.attempt_id,
                    entries.entry_json,
                ],
                select(
                    claims.scope,
                    runs.job,
                    claims.trial,
                    starts.started_at,
                    claims.id,
                    starts.id,
                    literal(entry_json),
                )
                .select_from(claims)
                .join(runs, runs.id == claims.run_id)
                .join(starts, starts.claim_id == claims.id)
                .where(
                    claims.id == claim_id,
                    claims.state.in_(['claimed', 'uncertain']),
                    starts.started_at == started_at,
                ),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    entries.scope,
                    entries.job,
                    entries.trial,
                    entries.started_at,
                ]
            ),
        )
        matched = fetch_one(
            self._database,
            select(entries.claim_id, entries.entry_json).where(
                entries.scope == claim.scope,
                entries.job == run.job,
                entries.trial == claim.trial,
                entries.started_at == started_at,
            ),
            _EntryMatch,
        )
        if (
            matched is None
            or matched.claim_id != claim_id
            or not _same_json(matched.entry_json, entry_json)
        ):
            raise InputError(
                f'Claim `{claim_id}` needs a matching live start or identical Entry.'
            )

    def entries(self, scope: str) -> list[str]:
        entries = schema.AttemptEntries
        return [
            str(row['entry_json'])
            for row in execute(
                self._database,
                select(entries.entry_json)
                .where(entries.scope == scope)
                .order_by(literal_column('rowid')),
            ).rows
        ]

    def accounting(self, scope: str) -> list[Row]:
        """Read final entries and uncovered admitted requests in one snapshot."""
        entries = aliased(schema.AttemptEntries, name='entries')
        requests = aliased(schema.RequestAdmissions, name='requests')
        starts = aliased(schema.AttemptStarts, name='starts')
        claims = aliased(schema.AttemptClaims, name='claims')
        runs = schema.Runs
        receipts = aliased(schema.RequestReceipts, name='receipts')
        covering = aliased(schema.AttemptEntries, name='covering')

        entry_rows = select(
            literal('entry').label('kind'),
            entries.entry_json,
            null().cast(Text).label('attempt_id'),
            entries.job,
            entries.trial,
            null().cast(Text).label('accounting_json'),
            null().cast(Text).label('receipt_json'),
            entries.started_at.label('started_at'),
            literal('').label('request_id'),
        ).where(entries.scope == scope)

        request_rows = (
            select(
                literal('request'),
                null().cast(Text),
                starts.id,
                runs.job,
                claims.trial,
                claims.accounting_json,
                receipts.receipt_json,
                starts.started_at,
                requests.id,
            )
            .select_from(requests)
            .join(starts, starts.id == requests.attempt_id)
            .join(claims, claims.id == starts.claim_id)
            .join(runs, runs.id == claims.run_id)
            .outerjoin(receipts, receipts.request_id == requests.id)
            .where(
                claims.scope == scope,
                ~exists(
                    select(literal(1)).where(
                        covering.attempt_id == starts.id,
                        covering.started_at == starts.started_at,
                    )
                ),
            )
        )
        combined = union_all(entry_rows, request_rows)
        return execute(
            self._database,
            combined.order_by(
                combined.selected_columns.started_at,
                combined.selected_columns.job,
                combined.selected_columns.trial,
                combined.selected_columns.request_id,
            ),
        ).rows

    def record_pending_artifact(
        self,
        run_id: str,
        trial_id: str,
        source_trial_id: str,
        staged_commit_sha: str,
        path: str,
        manifest_digest: str,
    ) -> None:
        expected = (
            run_id,
            trial_id,
            source_trial_id,
            staged_commit_sha,
            path,
            manifest_digest,
        )
        existing = next(
            (row for row in self.pending_artifacts(run_id) if row.trial_id == trial_id),
            None,
        )
        if existing is None:
            pending = schema.PendingTrialArtifacts
            execute(
                self._database,
                sqlite.insert(pending)
                .values(
                    run_id=run_id,
                    trial_id=trial_id,
                    source_trial_id=source_trial_id,
                    staged_commit_sha=staged_commit_sha,
                    path=path,
                    manifest_digest=manifest_digest,
                )
                .on_conflict_do_nothing(
                    index_elements=[pending.run_id, pending.trial_id]
                ),
            )
        row = next(
            row for row in self.pending_artifacts(run_id) if row.trial_id == trial_id
        )
        if (
            row.run_id,
            row.trial_id,
            row.source_trial_id,
            row.staged_commit_sha,
            row.path,
            row.manifest_digest,
        ) != expected:
            raise InputError(
                f'Trial `{trial_id}` already has different pending artifacts.'
            )

    def pending_artifacts(self, run_id: str) -> tuple[PendingArtifact, ...]:
        pending = schema.PendingTrialArtifacts
        return tuple(
            fetch_all(
                self._database,
                select(
                    pending.run_id,
                    pending.trial_id,
                    pending.source_trial_id,
                    pending.staged_commit_sha,
                    pending.path,
                    pending.manifest_digest,
                    pending.authorized_claim_id,
                    pending.publication_ready,
                    pending.canonical_commit_sha,
                )
                .where(pending.run_id == run_id)
                .order_by(pending.trial_id),
                PendingArtifact,
            )
        )

    def pending_runs(self) -> tuple[Run, ...]:
        pending = aliased(schema.PendingTrialArtifacts, name='pending')
        runs = schema.Runs
        rows = execute(
            self._database,
            select(pending.run_id)
            .distinct()
            .join(runs, runs.id == pending.run_id)
            .where(
                or_(
                    pending.canonical_commit_sha.is_(None),
                    runs.status != 'complete',
                )
            )
            .order_by(pending.run_id),
        ).rows
        return tuple(self.run(str(row['run_id'])) for row in rows)

    def run_systems(self, run_id: str) -> tuple[str, ...]:
        claims = schema.AttemptClaims
        rows = execute(
            self._database,
            select(claims.system).distinct().where(claims.run_id == run_id),
        ).rows
        return tuple(str(row['system']) for row in rows)

    def authorize_pending_claim(self, claim_id: str) -> None:
        claim = self.get_claim(claim_id)
        pending = schema.PendingTrialArtifacts
        rows = execute(
            self._database,
            update(pending)
            .values(authorized_claim_id=claim_id)
            .where(pending.run_id == claim.run_id, pending.trial_id == claim.trial)
            .returning(pending.authorized_claim_id),
        ).rows
        if not rows:
            raise InputError(f'Claim `{claim_id}` has no pending artifact.')

    def seal_publication(self, run_id: str) -> None:
        pending = schema.PendingTrialArtifacts
        execute(
            self._database,
            update(pending).values(publication_ready=1).where(pending.run_id == run_id),
        )

    def finalize_publication(self, run_id: str, canonical_commit_sha: str) -> None:
        if any(
            row.canonical_commit_sha not in (None, canonical_commit_sha)
            for row in self.pending_artifacts(run_id)
        ):
            raise InputError('The publication already names another canonical commit.')
        pending = schema.PendingTrialArtifacts
        execute(
            self._database,
            update(pending)
            .values(canonical_commit_sha=canonical_commit_sha)
            .where(
                pending.run_id == run_id,
                pending.canonical_commit_sha.is_distinct_from(canonical_commit_sha),
            ),
        )

    def pending_claims_authorized(self, run_id: str) -> bool:
        claims = aliased(schema.AttemptClaims, name='claims')
        pending = aliased(schema.PendingTrialArtifacts, name='pending')
        return not execute(
            self._database,
            select(literal(1))
            .select_from(claims)
            .outerjoin(
                pending,
                and_(
                    pending.run_id == claims.run_id,
                    pending.trial_id == claims.trial,
                ),
            )
            .where(
                claims.run_id == run_id,
                claims.state.not_in(['settled', 'cancelled']),
                or_(
                    pending.authorized_claim_id.is_distinct_from(claims.id),
                    pending.publication_ready != 1,
                ),
            ),
        ).rows

    def trial_artifact(self, run_id: str, trial_id: str) -> Row:
        artifacts = schema.TrialArtifacts
        rows = execute(
            self._database,
            select(artifacts).where(
                artifacts.run_id == run_id, artifacts.trial_id == trial_id
            ),
        ).rows
        if not rows:
            raise InputError(f'Trial `{trial_id}` has no registered artifacts.')
        return rows[0]

    def trial_registered(
        self, run_id: str, trial_id: str, source_trial_id: str
    ) -> bool:
        """Confirm an audited archive for the exact Harbor execution UUID."""
        artifacts = schema.TrialArtifacts
        return bool(
            execute(
                self._database,
                select(literal(1)).where(
                    artifacts.run_id == run_id,
                    artifacts.trial_id == trial_id,
                    artifacts.source_trial_id == source_trial_id,
                ),
            ).rows
        )

    def artifact_generation(self, scope: str) -> ArtifactGeneration:
        """Read the registry and its admission fence in one statement snapshot."""
        source_heads = schema.SourceHeads
        artifacts = schema.TrialArtifacts
        runs = schema.Runs
        head = select(
            func.coalesce(
                select(source_heads.revision)
                .where(source_heads.scope == scope)
                .scalar_subquery(),
                0,
            ).label('revision')
        ).cte('head')
        selected = (
            select(
                artifacts.run_id,
                artifacts.trial_id,
                artifacts.source_trial_id,
                artifacts.commit_sha,
                artifacts.path,
                artifacts.manifest_digest,
                runs.created_at,
            )
            .join(runs, runs.id == artifacts.run_id)
            .where(runs.scope == scope)
            .cte('selected')
        )
        rows = fetch_all(
            self._database,
            select(
                head.c.revision,
                selected.c.run_id,
                selected.c.trial_id,
                selected.c.source_trial_id,
                selected.c.commit_sha,
                selected.c.path,
                selected.c.manifest_digest,
            )
            .select_from(head.outerjoin(selected, literal(True)))
            .order_by(selected.c.created_at, selected.c.run_id, selected.c.trial_id),
            _ArtifactGenerationRow,
        )
        return ArtifactGeneration(
            revision=rows[0].revision,
            artifacts=tuple(
                TrialArtifact.model_validate(row.model_dump(exclude={'revision'}))
                for row in rows
                if row.run_id is not None
            ),
        )

    def complete_run(self, run_id: str) -> bool:
        """Close a run whose claims settled or were cancelled before execution.

        This closes claim lifecycles. Collection sealing separately establishes
        whether a complete set of trial outputs has been published.
        """
        self._run(run_id)
        runs = schema.Runs
        claims = aliased(schema.AttemptClaims, name='claims')
        artifacts = aliased(schema.ClaimArtifacts, name='artifacts')
        return bool(
            execute(
                self._database,
                update(runs)
                .values(status='complete')
                .where(
                    runs.id == run_id,
                    ~exists(
                        select(literal(1))
                        .select_from(claims)
                        .outerjoin(artifacts, artifacts.claim_id == claims.id)
                        .where(
                            claims.run_id == runs.id,
                            or_(
                                claims.state.not_in(['settled', 'cancelled']),
                                and_(
                                    claims.state == 'settled',
                                    artifacts.claim_id.is_(None),
                                ),
                            ),
                        )
                    ),
                )
                .returning(runs.id),
            ).rows
        )

    def run_status(self, run_id: str) -> RunStatus:
        return self._stored_run(run_id).status

    def run(self, run_id: str) -> Run:
        return self._run(run_id)

    # ── Output Generations ──

    def collection(self, collection_id: str) -> Collection:
        stored = self._stored_collection(collection_id)
        return Collection.model_validate(stored.model_dump(exclude={'sealed'}))

    def begin_collection(self, collection: Collection) -> None:
        collections = schema.Collections
        execute(
            self._database,
            sqlite.insert(collections)
            .values(
                id=collection.id,
                family=collection.family,
                expected_count=collection.expected_count,
                manifest_digest=collection.manifest_digest,
                created_at=collection.created_at,
                code_revision=collection.code_revision,
                case_revision=collection.case_revision,
                system_names_json=collection.system_names_json,
            )
            .on_conflict_do_nothing(index_elements=[collections.id]),
        )
        if self.collection(collection.id) != collection:
            raise InputError(
                f'Collection `{collection.id}` already has different metadata.'
            )

    def stage_outputs(
        self, collection: str, outputs: Sequence[tuple[int, str]]
    ) -> None:
        table = schema.CollectionOutputs
        for start in range(0, len(outputs), OUTPUTS_PER_STATEMENT):
            batch: list[tuple[int, str, str]] = []
            for position, output_json in outputs[start : start + OUTPUTS_PER_STATEMENT]:
                output = _object(output_json)
                key = json.dumps(
                    [output[name] for name in ('case', 'system', 'job', 'trial')],
                    ensure_ascii=False,
                    separators=(',', ':'),
                )
                batch.append((position, key, output_json))
            incoming = (
                values(
                    column('collection_id', Text),
                    column('position', Integer),
                    column('output_key', Text),
                    column('output_json', Text),
                    name='incoming',
                )
                .data(
                    [
                        (collection, position, key, output_json)
                        for position, key, output_json in batch
                    ]
                )
                .cte('incoming')
            )
            existing_rows = aliased(table, name='existing')
            execute(
                self._database,
                sqlite.insert(table).from_select(
                    [
                        table.collection_id,
                        table.position,
                        table.output_key,
                        table.output_json,
                    ],
                    select(
                        incoming.c.collection_id,
                        incoming.c.position,
                        incoming.c.output_key,
                        incoming.c.output_json,
                    ).where(
                        ~exists(
                            select(existing_rows.position).where(
                                existing_rows.collection_id == incoming.c.collection_id,
                                existing_rows.position == incoming.c.position,
                            )
                        )
                    ),
                ),
            )
            rows = fetch_all(
                self._database,
                select(table.position, table.output_key, table.output_json).where(
                    table.collection_id == collection,
                    table.position.in_([position for position, _, _ in batch]),
                ),
                _StagedOutput,
            )
            existing = {row.position: row for row in rows}
            for position, key, output_json in batch:
                row = existing[position]
                if row.output_key != key or not _same_json(
                    row.output_json, output_json
                ):
                    raise InputError(
                        f'Collection `{collection}` already has '
                        'a different output there.'
                    )

    def seal_collection(self, collection: str) -> None:
        stored = self._stored_collection(collection)
        if stored.sealed:
            return
        table = schema.CollectionOutputs
        outputs = fetch_all(
            self._database,
            select(table.position, table.output_json)
            .where(table.collection_id == collection)
            .order_by(table.position),
            _PositionOutput,
        )
        digest = hashlib.sha256()
        for output in outputs:
            digest.update((output.output_json + '\n').encode())
        if (
            len(outputs) != stored.expected_count
            or [item.position for item in outputs] != list(range(stored.expected_count))
            or digest.hexdigest() != stored.manifest_digest
        ):
            raise InputError(
                f'Collection `{collection}` does not match its complete manifest.'
            )

        collections = schema.Collections
        sealed = execute(
            self._database,
            update(collections)
            .values(sealed=1)
            .where(
                collections.id == collection,
                collections.sealed == 0,
                select(func.count())
                .select_from(table)
                .where(table.collection_id == collection)
                .scalar_subquery()
                == collections.expected_count,
            )
            .returning(collections.id),
        ).rows
        if not sealed and not self._stored_collection(collection).sealed:
            raise InputError(
                f'Collection `{collection}` changed before it could be sealed.'
            )

    def activate_collection(
        self, collection: str, expected_previous: str | None
    ) -> bool:
        stored = self._stored_collection(collection)
        if not stored.sealed:
            raise InputError(
                f'Collection `{collection}` must be sealed before activation.'
            )
        active = schema.ActiveCollections
        if expected_previous is None:
            changed = execute(
                self._database,
                sqlite.insert(active)
                .values(family=stored.family, collection_id=collection)
                .on_conflict_do_nothing(index_elements=[active.family])
                .returning(active.collection_id),
            ).rows
        else:
            changed = execute(
                self._database,
                update(active)
                .values(collection_id=collection)
                .where(
                    active.family == stored.family,
                    active.collection_id == expected_previous,
                )
                .returning(active.collection_id),
            ).rows
        if changed:
            return True
        current = fetch_scalar(
            self._database,
            select(active.collection_id).where(active.family == stored.family),
            str,
        )
        return current == collection

    def output_generation(self, family: str) -> OutputGeneration:
        """Read the active generation and its ordered outputs in one SQL snapshot."""
        active = aliased(schema.ActiveCollections, name='active')
        outputs = aliased(schema.CollectionOutputs, name='outputs')
        rows = fetch_all(
            self._database,
            select(active.collection_id, outputs.output_json)
            .select_from(active)
            .outerjoin(outputs, outputs.collection_id == active.collection_id)
            .where(active.family == family)
            .order_by(outputs.position),
            _GenerationRow,
        )
        return _generation(rows)

    def collection_generation(self, collection: str) -> OutputGeneration:
        """Read one complete immutable generation, refusing an unsealed import."""
        collections = schema.Collections
        outputs = aliased(schema.CollectionOutputs, name='outputs')
        rows = fetch_all(
            self._database,
            select(collections.id.label('collection_id'), outputs.output_json)
            .select_from(collections)
            .outerjoin(outputs, outputs.collection_id == collections.id)
            .where(collections.id == collection, collections.sealed == 1)
            .order_by(outputs.position),
            _GenerationRow,
        )
        if not rows:
            raise InputError(f'Collection `{collection}` is not sealed.')
        return _generation(rows)

    # ── Canary History and Baselines ──

    def append_canary(self, run: str, record_json: str) -> None:
        record = _object(record_json)
        records = schema.CanaryRecords
        execute(
            self._database,
            sqlite.insert(records)
            .values(
                run_id=run,
                system=cast(str, record['system']),
                record_json=record_json,
            )
            .on_conflict_do_nothing(index_elements=[records.run_id]),
        )
        stored = fetch_scalar(
            self._database,
            select(records.record_json).where(records.run_id == run),
            str,
        )
        if stored is None or not _same_json(stored, record_json):
            raise InputError(f'Canary run `{run}` already has a different outcome.')

    def records(self, system: str) -> list[str]:
        records = schema.CanaryRecords
        return [
            str(row['record_json'])
            for row in execute(
                self._database,
                select(records.record_json)
                .where(records.system == system)
                .order_by(
                    func.json_extract(records.record_json, '$.time'),
                    records.run_id,
                ),
            ).rows
        ]

    def baseline(self, system: str, context_key: str) -> str | None:
        records = aliased(schema.CanaryRecords, name='records')
        baselines = aliased(schema.CanaryBaselines, name='baselines')
        return fetch_scalar(
            self._database,
            select(records.record_json)
            .select_from(records)
            .join(baselines, baselines.run_id == records.run_id)
            .where(baselines.system == system, baselines.context_key == context_key),
            str,
        )

    def establish_baseline(self, system: str, context_key: str, run: str) -> bool:
        """Keep the first baseline the caller has accepted as successful."""
        baselines = schema.CanaryBaselines
        records = schema.CanaryRecords
        return bool(
            execute(
                self._database,
                sqlite.insert(baselines)
                .from_select(
                    [baselines.system, baselines.context_key, baselines.run_id],
                    select(
                        records.system,
                        literal(context_key),
                        records.run_id,
                    ).where(records.system == system, records.run_id == run),
                )
                .on_conflict_do_nothing(
                    index_elements=[baselines.system, baselines.context_key]
                )
                .returning(baselines.run_id),
            ).rows
        )

    def reset_baseline(self, system: str) -> int:
        baselines = schema.CanaryBaselines
        return len(
            execute(
                self._database,
                delete(baselines)
                .where(baselines.system == system)
                .returning(baselines.context_key),
            ).rows
        )

    # ── Stored Metadata ──

    def _stored_run(self, run_id: str) -> _StoredRun:
        runs = schema.Runs
        stored = fetch_one(
            self._database,
            select(
                runs.id,
                runs.scope,
                runs.job,
                runs.code_revision,
                runs.case_revision,
                runs.created_at,
                runs.max_requests,
                runs.status,
            ).where(runs.id == run_id),
            _StoredRun,
        )
        if stored is None:
            raise InputError(f'Run `{run_id}` is not recorded.')
        return stored

    def _run(self, run_id: str) -> Run:
        stored = self._stored_run(run_id)
        return Run.model_validate(stored.model_dump(exclude={'status'}))

    def _stored_claim(self, claim_id: str) -> _StoredClaim:
        claims = schema.AttemptClaims
        stored = fetch_one(
            self._database,
            select(
                claims.id,
                claims.run_id,
                claims.scope,
                claims.slot_key,
                claims.trial,
                claims.system,
                claims.fingerprint,
                claims.accounting_json,
                claims.expected_source_revision,
                claims.created_at,
                claims.state,
            ).where(claims.id == claim_id),
            _StoredClaim,
        )
        if stored is None:
            raise InputError(f'Claim `{claim_id}` is not recorded.')
        return stored

    def _stored_collection(self, collection: str) -> _StoredCollection:
        collections = schema.Collections
        stored = fetch_one(
            self._database,
            select(
                collections.id,
                collections.family,
                collections.expected_count,
                collections.manifest_digest,
                collections.created_at,
                collections.code_revision,
                collections.case_revision,
                collections.system_names_json,
                collections.sealed,
            ).where(collections.id == collection),
            _StoredCollection,
        )
        if stored is None:
            raise InputError(f'Collection `{collection}` is not recorded.')
        return stored


def _generation(rows: Sequence[_GenerationRow]) -> OutputGeneration:
    return OutputGeneration(
        id=rows[0].collection_id if rows else None,
        outputs=tuple(row.output_json for row in rows if row.output_json is not None),
    )


def _object(text: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads(text))


def _same_json(first: str, second: str) -> bool:
    return _object(first) == _object(second)
