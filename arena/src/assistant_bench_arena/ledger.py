"""Prepared domain commands for the application's single D1 owner."""

import json
from typing import Literal, Protocol

from pydantic import BaseModel, JsonValue

from assistant_bench_arena.ledger_models import (
    Accept,
    Actor,
    Admission,
    AdmissionResult,
    Case,
    Context,
    Credential,
    Cursor,
    Evaluation,
    Event,
    Export,
    ExportSection,
    ImportRow,
    ImportSource,
    Issue,
    Lease,
    Mode,
    Moderation,
    NumericalInput,
    Output,
    Page,
    PointSnapshot,
    Publication,
    Quota,
    Receipt,
    RenderedOutput,
    Row,
    Session,
    SqlValue,
    Statement,
    System,
    canonical,
    digest,
)


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
    'private_material',
    'publication_unsealed',
    'proof_consumed',
    'quota_exhausted',
    'quota_policy',
    'redacted',
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


_PAGE_ROWS = 250
_PAGE_BYTES = 1_048_576


class Ledger:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def _rows(self, sql: str, *params: SqlValue) -> list[Row]:
        return await self.transport.query(Statement(sql=sql, params=params))

    async def _execute(self, sql: str, *params: SqlValue) -> None:
        await self.transport.execute(Statement(sql=sql, params=params))

    async def _fact(self, lookup: Statement, insert: Statement, expected: Row) -> None:
        rows = await self.transport.query(lookup)

        if not rows:
            try:
                await self.transport.execute(insert)
                return
            except CommandRejected as error:
                if error.reason != 'immutable':
                    raise

            rows = await self.transport.query(lookup)

        if rows != [expected]:
            raise CommandRejected('conflict')

    async def initialize(
        self,
        namespace: str,
        mode: Mode,
        quotas: tuple[Quota, ...],
        *,
        attribution_ms: int | None = None,
    ) -> None:
        """Initialize an empty database before any serving command is exposed."""
        if mode == 'public' and (attribution_ms is None or attribution_ms <= 0):
            raise ValueError(
                'A public profile requires an explicit attribution lifetime.'
            )

        lifetime = attribution_ms or 0
        quota_body = canonical(
            [
                quota.model_dump(mode='json')
                for quota in sorted(
                    quotas,
                    key=lambda value: (value.operation, value.scope, value.window_ms),
                )
            ]
        )
        await self._fact(
            Statement(
                sql="""
                    SELECT namespace, mode, quota_body, attribution_ms
                    FROM deployment WHERE id = 1
                    """
            ),
            Statement(
                sql="""
                    INSERT INTO deployment(id, namespace, mode, quota_body,
                        attribution_ms, schema_version) VALUES (1, ?, ?, ?, ?, 1)
                    """,
                params=(namespace, mode, quota_body, lifetime),
            ),
            {
                'namespace': namespace,
                'mode': mode,
                'quota_body': quota_body,
                'attribution_ms': lifetime,
            },
        )

    async def stage_policy(self, policy_id: str, policy: dict[str, JsonValue]) -> None:
        body = canonical(policy)
        await self._fact(
            Statement(
                sql='SELECT body FROM policies WHERE id = ?', params=(policy_id,)
            ),
            Statement(
                sql='INSERT INTO policies(id, body) VALUES (?, ?)',
                params=(policy_id, body),
            ),
            {'body': body},
        )

    async def stage_evaluation(self, evaluation: Evaluation) -> None:
        body = canonical(evaluation)
        await self._fact(
            Statement(
                sql='SELECT body FROM evaluations WHERE id = ?', params=(evaluation.id,)
            ),
            Statement(
                sql="""
                    INSERT INTO evaluations(id, family, policy_id, body,
                    case_count, system_count, output_count, numerical_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    evaluation.id,
                    evaluation.family,
                    evaluation.policy_id,
                    body,
                    evaluation.cases,
                    evaluation.systems,
                    evaluation.outputs,
                    evaluation.numerics,
                ),
            ),
            {'body': body},
        )

    async def stage_case(self, evaluation_id: str, case: Case) -> None:
        body = canonical(case)
        await self._fact(
            Statement(
                sql="""
                    SELECT body FROM evaluation_cases WHERE evaluation_id = ?
                    AND case_key = ?
                    """,
                params=(evaluation_id, case.key),
            ),
            Statement(
                sql="""
                    INSERT INTO evaluation_cases(evaluation_id, case_key,
                    category, source, instruction_digest, body) VALUES (?, ?, ?,
                    ?, ?, ?)
                    """,
                params=(
                    evaluation_id,
                    case.key,
                    case.category,
                    case.source,
                    case.instruction_digest,
                    body,
                ),
            ),
            {'body': body},
        )

    async def stage_system(self, evaluation_id: str, system: System) -> None:
        body = canonical(system)
        await self._fact(
            Statement(
                sql="""
                    SELECT body FROM evaluation_systems WHERE evaluation_id = ?
                    AND system_id = ?
                    """,
                params=(evaluation_id, system.id),
            ),
            Statement(
                sql="""
                    INSERT INTO evaluation_systems(evaluation_id, system_id,
                    fingerprint, declared_order, body) VALUES (?, ?, ?, ?, ?)
                    """,
                params=(
                    evaluation_id,
                    system.id,
                    system.fingerprint,
                    system.order,
                    body,
                ),
            ),
            {'body': body},
        )

    async def stage_output(self, evaluation_id: str, output: Output) -> None:
        body = canonical(output)
        await self._fact(
            Statement(
                sql="""
                    SELECT body FROM evaluation_outputs WHERE evaluation_id = ?
                    AND output_key = ?
                    """,
                params=(evaluation_id, output.key),
            ),
            Statement(
                sql="""
                    INSERT INTO evaluation_outputs(evaluation_id, output_key,
                    case_key, system_id, content_digest, verdict, output_length,
                    approved_public, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    evaluation_id,
                    output.key,
                    output.case_key,
                    output.system_id,
                    output.content_digest,
                    output.verdict,
                    output.length,
                    int(output.approved_public),
                    body,
                ),
            ),
            {'body': body},
        )

    async def stage_numerical(self, evaluation_id: str, value: NumericalInput) -> None:
        body = canonical(value)
        await self._fact(
            Statement(
                sql="""
                    SELECT body FROM evaluation_numerics WHERE evaluation_id = ?
                    AND section = ? AND row_key = ?
                    """,
                params=(evaluation_id, value.section, value.key),
            ),
            Statement(
                sql="""
                    INSERT INTO evaluation_numerics(evaluation_id, section,
                    row_key, body) VALUES (?, ?, ?, ?)
                    """,
                params=(evaluation_id, value.section, value.key, body),
            ),
            {'body': body},
        )

    async def seal_evaluation(self, evaluation_id: str, now: int) -> None:
        await self._execute(
            'INSERT INTO seal_evaluation(evaluation_id, now_ms) VALUES (?, ?)',
            evaluation_id,
            now,
        )

    async def stage_publication(self, publication: Publication) -> None:
        body = canonical(publication)
        await self._fact(
            Statement(
                sql='SELECT body FROM publications WHERE id = ?',
                params=(publication.id,),
            ),
            Statement(
                sql="""
                    INSERT INTO publications(id, evaluation_id, body,
                    output_count) VALUES (?, ?, ?, ?)
                    """,
                params=(
                    publication.id,
                    publication.evaluation_id,
                    body,
                    publication.outputs,
                ),
            ),
            {'body': body},
        )

    async def stage_rendered(self, publication_id: str, output: RenderedOutput) -> None:
        body = canonical(output)
        await self._fact(
            Statement(
                sql="""
                    SELECT body FROM rendered_outputs WHERE publication_id = ?
                    AND output_key = ?
                    """,
                params=(publication_id, output.output_key),
            ),
            Statement(
                sql="""
                    INSERT INTO rendered_outputs(publication_id, output_key,
                    render, content, content_digest, approved_public, body)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    publication_id,
                    output.output_key,
                    output.render,
                    output.content,
                    output.content_digest,
                    int(output.approved_public),
                    body,
                ),
            ),
            {'body': body},
        )

    async def seal_publication(self, publication_id: str, now: int) -> None:
        await self._execute(
            'INSERT INTO seal_publication(publication_id, now_ms) VALUES (?, ?)',
            publication_id,
            now,
        )

    async def stage_points(self, points: PointSnapshot, now: int) -> None:
        body = canonical(points)
        await self._fact(
            Statement(
                sql='SELECT body FROM point_snapshots WHERE id = ?', params=(points.id,)
            ),
            Statement(
                sql="""
                    INSERT INTO point_snapshots(id, evaluation_id, policy_id,
                    source_export_id, body, sealed_at) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    points.id,
                    points.evaluation_id,
                    points.policy_id,
                    points.source_export_id,
                    body,
                    now,
                ),
            ),
            {'body': body},
        )

    async def register_actor(self, actor: Actor) -> None:
        expected = actor.model_dump(mode='json')
        await self._fact(
            Statement(sql='SELECT * FROM actors WHERE id = ?', params=(actor.id,)),
            Statement(
                sql="""
                    INSERT INTO actors(id, created_at, owner_namespace, rate_subject)
                    VALUES (?, ?, ?, ?)
                    """,
                params=(
                    actor.id,
                    actor.created_at,
                    actor.owner_namespace,
                    actor.rate_subject,
                ),
            ),
            expected,
        )

    async def credential(self, credential_id: str, now: int) -> Credential | None:
        rows = await self._rows(
            """
            SELECT * FROM credentials WHERE id = ? AND revoked_at IS NULL
                AND expires_at > ?
            """,
            credential_id,
            now,
        )
        return Credential.model_validate(rows[0]) if rows else None

    async def admit(self, context: Context, admission: Admission) -> AdmissionResult:
        """Consume one verified proof and preserve the first signed-cookie result."""
        if context.principal != (
            admission.context_digest
            if admission.operation == 'new'
            else admission.actor_id
        ):
            raise CommandRejected('credential_context')

        request_hash = digest(
            {
                'operation': admission.operation,
                'principal': context.principal,
                'context_digest': admission.context_digest,
                'proof_digest': admission.proof_digest,
                'original_credential': admission.original_credential,
            }
        )
        result = await self._admission_result(context, request_hash)

        if result is not None:
            return result

        try:
            await self._execute(
                """
                INSERT INTO admit_credential(command_id, request_hash, operation,
                    context_digest, proof_digest, original_credential, actor_id,
                    credential_id, rate_subject, now_ms, expires_at, challenge_until,
                    network) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                context.command_id,
                request_hash,
                admission.operation,
                admission.context_digest,
                admission.proof_digest,
                admission.original_credential,
                admission.actor_id,
                admission.credential_id,
                admission.rate_subject,
                context.now,
                admission.expires_at,
                admission.challenge_until,
                canonical(admission.network.model_dump(mode='json')['tags']),
            )
        except CommandRejected as error:
            if error.reason != 'command_exists':
                raise

        result = await self._admission_result(context, request_hash)

        if result is None:
            raise RuntimeError('Admission completed without its credential result.')

        return result

    async def _admission_result(
        self, context: Context, request_hash: str
    ) -> AdmissionResult | None:
        rows = await self._rows(
            'SELECT * FROM admission_results WHERE command_id = ?', context.command_id
        )

        if not rows:
            return None

        result = AdmissionResult.model_validate(rows[0])

        if result.request_hash != request_hash:
            raise CommandRejected('conflict')

        if await self.credential(result.credential_id, context.now) is None:
            raise CommandRejected('credential_context')

        if result.original_credential is not None:
            original = await self._rows(
                """
                SELECT id FROM credentials WHERE id = ? AND actor_id = ?
                    AND renewal_command = ? AND revoked_at IS NOT NULL
                """,
                result.original_credential,
                context.principal,
                context.command_id,
            )

            if not original:
                raise CommandRejected('credential_context')

        return result

    async def start_session(self, session: Session) -> None:
        await self._fact(
            Statement(sql='SELECT * FROM sessions WHERE id = ?', params=(session.id,)),
            Statement(
                sql="""
                    INSERT INTO sessions(id, actor_id, family, created_at,
                    import_source) VALUES (?, ?, ?, ?, ?)
                    """,
                params=(
                    session.id,
                    session.actor_id,
                    session.family,
                    session.created_at,
                    session.import_source,
                ),
            ),
            session.model_dump(mode='json'),
        )

    async def _event(self, context: Context, request_hash: str) -> Event | None:
        rows = await self._rows(
            'SELECT * FROM evidence_events WHERE command_id = ?', context.command_id
        )

        if not rows:
            return None

        row = rows[0]

        if row['principal'] != context.principal or row['request_hash'] != request_hash:
            raise CommandRejected('conflict')

        return _decode(Event, row, 'data')

    async def _command(
        self, context: Context, request_hash: str, statement: Statement
    ) -> Event:
        existing = await self._event(context, request_hash)

        if existing is not None:
            return existing

        try:
            await self.transport.execute(statement)
        except CommandRejected as error:
            if error.reason != 'command_exists':
                raise

        event = await self._event(context, request_hash)

        if event is None:
            raise RuntimeError('A ledger command completed without its evidence event.')

        return event

    async def activate(
        self,
        context: Context,
        family: str,
        revision: int,
        evaluation_id: str,
        publication_id: str,
        point_id: str,
    ) -> Event:
        request_hash = digest(
            {
                'kind': 'activate',
                'family': family,
                'revision': revision,
                'evaluation_id': evaluation_id,
                'publication_id': publication_id,
                'point_id': point_id,
            }
        )
        return await self._command(
            context,
            request_hash,
            Statement(
                sql="""
                    INSERT INTO activate_family(command_id, request_hash,
                    principal, now_ms, family, revision, evaluation_id,
                    publication_id, point_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    context.command_id,
                    request_hash,
                    context.principal,
                    context.now,
                    family,
                    revision,
                    evaluation_id,
                    publication_id,
                    point_id,
                ),
            ),
        )

    async def resume(self, actor_id: str, session_id: str, now: int) -> Lease | None:
        rows = await self._rows(
            """
                SELECT l.* FROM compatible_leases l JOIN session_state s ON
                s.current_lease = l.id WHERE l.actor_id = ? AND l.session_id = ?
                AND l.status = 'pending' AND l.expires_at > ?
                """,
            actor_id,
            session_id,
            now,
        )
        return Lease.model_validate(rows[0]) if rows else None

    async def issue(self, context: Context, issue: Issue) -> Lease:
        semantic = issue.model_dump(mode='json', exclude={'network', 'expires_at'})
        request_hash = digest(
            {'kind': 'issue', 'actor_id': context.principal, 'request': semantic}
        )
        existing = await self._rows(
            'SELECT * FROM pair_leases WHERE command_id = ?', context.command_id
        )

        if existing:
            return _lease(existing[0], context.principal, request_hash)

        systems = await self._rows(
            """
                SELECT o.output_key, o.system_id FROM evaluation_outputs o JOIN
                family_state f ON f.evaluation_id = o.evaluation_id JOIN
                sessions s ON s.family = f.family WHERE s.id = ? AND
                o.output_key IN (?, ?)
                """,
            issue.session_id,
            issue.a_output,
            issue.b_output,
        )

        if len(systems) != 2:
            raise CommandRejected('candidate_stale')

        comparison_key = (
            canonical(
                [issue.case_key, *sorted(str(row['system_id']) for row in systems)]
            )
            if issue.repeat_of is None
            else str(issue.repeat_of)
        )

        try:
            await self._execute(
                """
                    INSERT INTO issue_pair(command_id, request_hash, actor_id,
                    lease_id, session_id, session_version, claim_id,
                    claim_version, case_key, a_output, b_output, comparison_key,
                    repeat_of, now_ms, expires_at, network) VALUES (?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                context.command_id,
                request_hash,
                context.principal,
                issue.lease_id,
                issue.session_id,
                issue.session_version,
                issue.claim_id,
                issue.claim_version,
                issue.case_key,
                issue.a_output,
                issue.b_output,
                comparison_key,
                issue.repeat_of,
                context.now,
                issue.expires_at,
                canonical(issue.network.model_dump(mode='json')['tags']),
            )
        except CommandRejected as error:
            if error.reason == 'pending_exists':
                current = await self.resume(
                    context.principal, issue.session_id, context.now
                )

                if current is not None:
                    return current

            if error.reason != 'command_exists':
                raise

        rows = await self._rows(
            'SELECT * FROM pair_leases WHERE command_id = ?', context.command_id
        )

        if not rows:
            raise RuntimeError('Pair issuance completed without a durable lease.')

        return _lease(rows[0], context.principal, request_hash)

    async def accept(self, context: Context, vote: Accept) -> Receipt:
        defects: dict[str, JsonValue] = {
            'a': [str(value) for value in sorted(vote.defects.a)],
            'b': [str(value) for value in sorted(vote.defects.b)],
        }
        request_hash = digest(
            {
                'kind': 'accept',
                'actor_id': context.principal,
                'lease_id': vote.lease_id,
                'choice': vote.choice,
                'defects': defects,
            }
        )
        event = await self._command(
            context,
            request_hash,
            Statement(
                sql="""
                    INSERT INTO accept_vote(command_id, request_hash, actor_id,
                    lease_id, acknowledgment, choice, defects, now_ms, network,
                    attribution) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    context.command_id,
                    request_hash,
                    context.principal,
                    vote.lease_id,
                    vote.acknowledgment,
                    vote.choice,
                    canonical(defects),
                    context.now,
                    canonical(vote.network.model_dump(mode='json')['tags']),
                    canonical(
                        [value.model_dump(mode='json') for value in vote.attribution]
                    ),
                ),
            ),
        )
        rows = await self._rows('SELECT * FROM vote_receipts WHERE seq = ?', event.seq)

        if not rows:
            raise RuntimeError('An acceptance event has no receipt.')

        return _decode(Receipt, rows[0], 'defects')

    async def withdraw(
        self, context: Context, session_id: str, session_version: int, receipt_seq: int
    ) -> Event:
        request_hash = digest(
            {
                'kind': 'withdraw',
                'actor_id': context.principal,
                'session_id': session_id,
                'session_version': session_version,
                'receipt_seq': receipt_seq,
            }
        )
        return await self._command(
            context,
            request_hash,
            Statement(
                sql="""
                    INSERT INTO withdraw_latest(command_id, request_hash,
                    actor_id, session_id, session_version, receipt_seq, now_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    context.command_id,
                    request_hash,
                    context.principal,
                    session_id,
                    session_version,
                    receipt_seq,
                    context.now,
                ),
            ),
        )

    async def stage_moderation(self, batch: Moderation) -> None:
        header: Row = {
            'id': batch.id,
            'family': batch.family,
            'principal': batch.principal,
            'action': batch.action,
            'reason': batch.reason,
            'target_count': len(batch.targets),
            'target_digest': digest(
                [target.model_dump(mode='json') for target in batch.targets]
            ),
            'created_at': batch.created_at,
        }
        await self._fact(
            Statement(
                sql="""
                    SELECT id, family, principal, action, reason, target_count,
                    target_digest, created_at FROM moderation_batches WHERE id =
                    ?
                    """,
                params=(batch.id,),
            ),
            Statement(
                sql="""
                    INSERT INTO moderation_batches(id, family, principal,
                    action, reason, target_count, target_digest, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    batch.id,
                    batch.family,
                    batch.principal,
                    batch.action,
                    batch.reason,
                    len(batch.targets),
                    str(header['target_digest']),
                    batch.created_at,
                ),
            ),
            header,
        )

        for ordinal, target in enumerate(batch.targets):
            await self._fact(
                Statement(
                    sql="""
                        SELECT receipt_seq, claim_version, exclusion_seq FROM
                        moderation_targets WHERE batch_id = ? AND ordinal = ?
                        """,
                    params=(batch.id, ordinal),
                ),
                Statement(
                    sql="""
                        INSERT INTO moderation_targets(batch_id, ordinal,
                        receipt_seq, claim_version, exclusion_seq) VALUES (?, ?,
                        ?, ?, ?)
                        """,
                    params=(
                        batch.id,
                        ordinal,
                        target.receipt_seq,
                        target.claim_version,
                        target.exclusion_seq,
                    ),
                ),
                target.model_dump(mode='json'),
            )

    async def seal_moderation(self, batch_id: str, now: int) -> None:
        await self._execute(
            'INSERT INTO seal_moderation(batch_id, now_ms) VALUES (?, ?)', batch_id, now
        )

    async def moderate(
        self, context: Context, batch_id: str, chunk_index: int
    ) -> Event:
        request_hash = digest(
            {'kind': 'moderate', 'batch_id': batch_id, 'chunk_index': chunk_index}
        )
        return await self._command(
            context,
            request_hash,
            Statement(
                sql="""
                    INSERT INTO moderate_chunk(command_id, request_hash,
                    principal, now_ms, batch_id, chunk_index) VALUES (?, ?, ?,
                    ?, ?, ?)
                    """,
                params=(
                    context.command_id,
                    request_hash,
                    context.principal,
                    context.now,
                    batch_id,
                    chunk_index,
                ),
            ),
        )

    async def attribute(
        self, context: Context, family: str, revision: int, actor_id: str, owner_id: str
    ) -> Event:
        request_hash = digest(
            {
                'kind': 'attribute',
                'family': family,
                'revision': revision,
                'actor_id': actor_id,
                'owner_id': owner_id,
            }
        )
        return await self._command(
            context,
            request_hash,
            Statement(
                sql="""
                    INSERT INTO attribute_owner(command_id, request_hash,
                    principal, now_ms, family, revision, actor_id, owner_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                params=(
                    context.command_id,
                    request_hash,
                    context.principal,
                    context.now,
                    family,
                    revision,
                    actor_id,
                    owner_id,
                ),
            ),
        )

    async def freeze(self, export_id: str, family: str, now: int) -> Export:
        rows = await self._rows(
            'SELECT * FROM analysis_exports WHERE id = ?', export_id
        )

        if not rows:
            try:
                await self._execute(
                    'INSERT INTO freeze_export(id, family, now_ms) VALUES (?, ?, ?)',
                    export_id,
                    family,
                    now,
                )
            except CommandRejected as error:
                if error.reason != 'immutable':
                    raise

            rows = await self._rows(
                'SELECT * FROM analysis_exports WHERE id = ?', export_id
            )

        if not rows or rows[0]['family'] != family:
            raise CommandRejected('conflict')

        return _decode(Export, rows[0], 'policy')

    async def page(
        self,
        export_id: str,
        section: ExportSection,
        cursor: str | None = None,
        *,
        limit: int = _PAGE_ROWS,
    ) -> Page:
        """Read immutable keyset pages with at most 250 rows and 1 MiB of JSON."""
        if not 1 <= limit <= _PAGE_ROWS:
            raise ValueError('An export page must contain between 1 and 250 rows.')

        after = ''

        if cursor is not None:
            parsed = Cursor.model_validate_json(cursor)

            if parsed.export_id != export_id or parsed.section != section:
                raise ValueError('An export cursor belongs to a different section.')

            after = parsed.key

        sizes = await self._rows(
            """
            SELECT row_key, length(CAST(body AS BLOB)) AS bytes FROM export_rows
            WHERE export_id = ? AND section = ? AND row_key > ?
            ORDER BY row_key LIMIT ?
            """,
            export_id,
            section,
            after,
            limit,
        )

        if not sizes:
            return Page(rows=[], cursor=None)

        total = 2
        last = after

        for size in sizes:
            length = size['bytes']

            if not isinstance(length, int):
                raise TypeError('An export row has no encoded byte length.')

            if total + length + 1 > _PAGE_BYTES:
                break

            total += length + 1
            last = str(size['row_key'])

        if last == after:
            raise ValueError('An export row exceeds the 1 MiB page boundary.')

        encoded = await self._rows(
            """
            SELECT body FROM export_rows WHERE export_id = ? AND section = ?
                AND row_key > ? AND row_key <= ? ORDER BY row_key LIMIT ?
            """,
            export_id,
            section,
            after,
            last,
            limit,
        )
        rows: list[Row] = []

        for row in encoded:
            body = row['body']

            if not isinstance(body, str):
                raise TypeError('An export row is not encoded JSON.')

            value: JsonValue = json.loads(body)

            if not isinstance(value, dict):
                raise TypeError('An export row is not a JSON object.')

            rows.append(value)

        more = len(sizes) == limit or last != sizes[-1]['row_key']
        next_cursor = (
            canonical(Cursor(export_id=export_id, section=section, key=last))
            if more
            else None
        )
        return Page(rows=rows, cursor=next_cursor)

    async def register_import(self, source: ImportSource) -> None:
        await self._fact(
            Statement(
                sql="""
                    SELECT id, owner_id, source_schema, file_digest FROM
                    import_sources WHERE id = ?
                    """,
                params=(source.id,),
            ),
            Statement(
                sql="""
                    INSERT INTO import_sources(id, owner_id, source_schema,
                    file_digest) VALUES (?, ?, ?, ?)
                    """,
                params=(
                    source.id,
                    source.owner_id,
                    source.source_schema,
                    source.file_digest,
                ),
            ),
            source.model_dump(mode='json'),
        )

    async def preserve_import(self, source_id: str, row: ImportRow) -> None:
        """Retain raw lineage without asserting an unproved contemporary output."""
        expected: Row = {
            'target_id': row.target_id,
            'row_hash': row.source_hash,
            'raw': canonical(row.raw),
            'linkage': row.linkage,
        }
        await self._fact(
            Statement(
                sql="""
                    SELECT target_id, row_hash, raw, linkage FROM import_rows
                    WHERE source_id = ? AND row_kind = ? AND original_id = ?
                    """,
                params=(source_id, row.kind, row.original_id),
            ),
            Statement(
                sql="""
                    INSERT INTO import_rows(source_id, row_kind, original_id,
                    target_id, row_hash, raw, linkage) VALUES (?, ?, ?, ?, ?, ?,
                    ?)
                    """,
                params=(
                    source_id,
                    row.kind,
                    row.original_id,
                    row.target_id,
                    row.source_hash,
                    canonical(row.raw),
                    row.linkage,
                ),
            ),
            expected,
        )


def _decode[Record: BaseModel](model: type[Record], row: Row, field: str) -> Record:
    values = dict(row)
    encoded = values[field]

    if not isinstance(encoded, str):
        raise TypeError(f'Ledger field {field} is not encoded JSON.')

    values[field] = json.loads(encoded)
    return model.model_validate(values)


def _lease(row: Row, actor_id: str, request_hash: str) -> Lease:
    if row['actor_id'] != actor_id or row['request_hash'] != request_hash:
        raise CommandRejected('conflict')

    return Lease.model_validate(row)
