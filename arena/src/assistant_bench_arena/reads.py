"""Bounded reads of immutable releases through the sole database owner."""

from typing import Literal

from pydantic import BaseModel

from assistant_bench_arena.ledger_models import (
    Case,
    Evaluation,
    Mode,
    NumericalInput,
    Output,
    PointSnapshot,
    Publication,
    RenderedOutput,
    Statement,
    System,
)
from assistant_bench_arena.read_models import (
    CasePage,
    ClaimPage,
    ClaimState,
    Coverage,
    CoveragePage,
    DueRepeat,
    FamilyState,
    OwnedSession,
    RenderedContent,
    RepeatPage,
)
from assistant_bench_arena.releases import EvaluationBundle
from assistant_bench_arena.snapshots import AnalysisPolicy
from assistant_bench_arena.transport import CommandRejected, Transport


_PAGE_ROWS = 250
_BODY_PAGE_ROWS = 99
_PAGE_BYTES = 1_048_576


class LedgerReads:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def _one[T: BaseModel](self, table: str, identity: str, model: type[T]) -> T:
        rows = await self.transport.query(
            Statement(
                sql=f"""
                    SELECT CASE WHEN length(CAST(body AS BLOB)) <= ? THEN body END
                        AS body FROM {table} WHERE id = ?
                    """,
                params=(_PAGE_BYTES, identity),
            )
        )

        if not rows:
            raise CommandRejected('invalid_release')

        body = rows[0]['body']

        if not isinstance(body, str):
            raise ValueError('The immutable record exceeds the transport limit.')

        return model.model_validate_json(body)

    async def _records[T: BaseModel](
        self,
        table: str,
        scope: str,
        identity: str,
        key: str,
        model: type[T],
    ) -> tuple[T, ...]:
        result: list[T] = []
        after = ''

        while True:
            headers = await self.transport.query(
                Statement(
                    sql=f"""
                        SELECT {key} AS key, length(CAST(body AS BLOB)) AS bytes
                        FROM {table} WHERE {scope} = ? AND {key} > ?
                        ORDER BY {key} LIMIT ?
                        """,
                    params=(identity, after, _BODY_PAGE_ROWS),
                )
            )

            if not headers:
                return tuple(result)

            size = 0
            through = after
            keys: list[str] = []

            for header in headers:
                row_key = header['key']
                row_size = header['bytes']

                if not isinstance(row_key, str) or not isinstance(row_size, int):
                    raise TypeError('A release page must have typed keys and sizes.')

                if row_size > _PAGE_BYTES:
                    raise ValueError('One release record exceeds the transport limit.')

                if size + row_size > _PAGE_BYTES:
                    break

                size += row_size
                through = row_key
                keys.append(row_key)

            placeholders = ', '.join('?' for _ in keys)
            rows = await self.transport.query(
                Statement(
                    sql=f"""
                        SELECT body FROM {table} WHERE {scope} = ?
                            AND {key} IN ({placeholders}) ORDER BY {key}
                        """,
                    params=(identity, *keys),
                )
            )

            for row in rows:
                body = row['body']

                if not isinstance(body, str):
                    raise TypeError('A release record must contain a JSON body.')

                result.append(model.model_validate_json(body))

            after = through

    async def evaluation(self, identity: str) -> EvaluationBundle:
        """Read staged or sealed facts for validation and immutable-ID caching."""
        evaluation = await self._one('evaluations', identity, Evaluation)
        deployment = await self.transport.query(
            Statement(sql='SELECT mode FROM deployment WHERE id = 1')
        )

        if not deployment:
            raise CommandRejected('invalid_release')

        mode: Mode

        if deployment[0]['mode'] == 'local':
            mode = 'local'
        elif deployment[0]['mode'] == 'public':
            mode = 'public'
        else:
            raise TypeError('The deployment must have a supported mode.')

        return EvaluationBundle(
            mode=mode,
            policy=await self._one('policies', evaluation.policy_id, AnalysisPolicy),
            evaluation=evaluation,
            cases=await self._records(
                'evaluation_cases', 'evaluation_id', identity, 'case_key', Case
            ),
            systems=tuple(
                sorted(
                    await self._records(
                        'evaluation_systems',
                        'evaluation_id',
                        identity,
                        'system_id',
                        System,
                    ),
                    key=lambda system: system.order,
                )
            ),
            outputs=await self._records(
                'evaluation_outputs', 'evaluation_id', identity, 'output_key', Output
            ),
            numerics=tuple(
                sorted(
                    await self._records(
                        'evaluation_numerics',
                        'evaluation_id',
                        identity,
                        'json_array(section, row_key)',
                        NumericalInput,
                    ),
                    key=lambda row: (row.section, row.key),
                )
            ),
        )

    async def publication_metadata(self, identity: str) -> Publication:
        return await self._one('publications', identity, Publication)

    async def publication(
        self, identity: str
    ) -> tuple[Publication, tuple[RenderedOutput, ...]]:
        publication = await self.publication_metadata(identity)
        rendered = await self._records(
            'rendered_outputs', 'publication_id', identity, 'output_key', RenderedOutput
        )
        return publication, rendered

    async def family(self, family: str) -> FamilyState | None:
        rows = await self.transport.query(
            Statement(
                sql='SELECT * FROM family_state WHERE family = ?', params=(family,)
            )
        )
        return FamilyState.model_validate(rows[0]) if rows else None

    async def points(self, identity: str) -> PointSnapshot:
        return await self._one('point_snapshots', identity, PointSnapshot)

    async def session(self, actor_id: str, session_id: str) -> OwnedSession | None:
        rows = await self.transport.query(
            Statement(
                sql="""
                    SELECT s.id, s.actor_id, s.family, r.canonical_actor,
                        t.version, t.current_lease,
                        (SELECT count(*) FROM vote_receipts v
                            JOIN coverage_members m ON m.receipt_seq = v.seq
                            WHERE v.session_id = s.id) AS eligible_votes,
                        (SELECT v.acknowledgment FROM vote_receipts v
                            JOIN receipt_state state ON state.receipt_seq = v.seq
                            WHERE v.session_id = s.id AND state.withdrawn IS NULL
                            ORDER BY v.seq DESC LIMIT 1) AS undo_acknowledgment
                    FROM sessions s
                    JOIN session_state t ON t.session_id = s.id
                    JOIN actor_resolution r ON r.family = s.family
                        AND r.actor_id = s.actor_id
                    WHERE s.id = ? AND s.actor_id = ?
                    """,
                params=(session_id, actor_id),
            )
        )
        return OwnedSession.model_validate(rows[0]) if rows else None

    async def claims(self, family: str, actor_id: str, after: str = '') -> ClaimPage:
        rows = await self.transport.query(
            Statement(
                sql="""
                    SELECT c.id, c.kind, c.comparison_key, c.repeat_of, c.version,
                        c.selected_receipt, c.current_lease,
                        EXISTS (SELECT 1 FROM coverage_members m
                            WHERE m.claim_id = c.id) AS eligible
                    FROM claims c WHERE c.family = ? AND c.canonical_actor =
                        COALESCE((SELECT canonical_actor FROM actor_resolution
                            WHERE family = ? AND actor_id = ?), ?)
                        AND c.merged_into IS NULL AND c.id > ?
                    ORDER BY c.id LIMIT 251
                    """,
                params=(family, family, actor_id, actor_id, after),
            )
        )
        selected = rows[:_PAGE_ROWS]
        return ClaimPage(
            rows=tuple(
                ClaimState.model_validate({**row, 'eligible': row['eligible'] == 1})
                for row in selected
            ),
            after=str(selected[-1]['id']) if len(rows) > _PAGE_ROWS else None,
        )

    async def coverage(
        self, family: str, after: tuple[str, str] = ('', '')
    ) -> CoveragePage:
        rows = await self.transport.query(
            Statement(
                sql="""
                    SELECT a_system, b_system, count FROM coverage
                    WHERE family = ? AND (a_system, b_system) > (?, ?)
                    ORDER BY a_system, b_system LIMIT 251
                    """,
                params=(family, *after),
            )
        )
        selected = tuple(Coverage.model_validate(row) for row in rows[:_PAGE_ROWS])
        return CoveragePage(
            rows=selected,
            after=(selected[-1].a_system, selected[-1].b_system)
            if len(rows) > _PAGE_ROWS
            else None,
        )

    async def used_cases(
        self, actor_id: str, session_id: str, after: str = ''
    ) -> CasePage:
        rows = await self.transport.query(
            Statement(
                sql="""
                    SELECT c.case_key FROM session_cases c
                    JOIN sessions s ON s.id = c.session_id
                    WHERE s.id = ? AND s.actor_id = ? AND c.case_key > ?
                    ORDER BY c.case_key LIMIT 251
                    """,
                params=(session_id, actor_id, after),
            )
        )
        selected = tuple(str(row['case_key']) for row in rows[:_PAGE_ROWS])
        return CasePage(
            rows=selected,
            after=selected[-1] if len(rows) > _PAGE_ROWS else None,
        )

    async def render(
        self, lease_id: str, side: Literal['a', 'b'], now: int
    ) -> RenderedContent | None:
        rows = await self.transport.query(
            Statement(
                sql=f"""
                    SELECT r.render, r.content FROM compatible_leases l
                    JOIN claims c ON c.id = l.claim_id
                    JOIN session_state s ON s.session_id = l.session_id
                    JOIN rendered_outputs r ON r.publication_id = l.publication_id
                        AND r.output_key = l.{side}_output
                    WHERE l.id = ? AND l.status = 'pending' AND l.expires_at > ?
                        AND c.current_lease = l.id AND c.version = l.claim_version
                        AND s.current_lease = l.id AND c.merged_into IS NULL
                        AND length(CAST(r.content AS BLOB)) <= ?
                    """,
                params=(lease_id, now, _PAGE_BYTES),
            )
        )
        return RenderedContent.model_validate(rows[0]) if rows else None

    async def due_repeats(
        self, family: str, actor_id: str, now: int, after: int = 0
    ) -> RepeatPage:
        rows = await self.transport.query(
            Statement(
                sql="""
                    SELECT r.seq AS receipt_seq, r.case_key, r.b_output AS a_output,
                        r.a_output AS b_output, c.id AS claim_id,
                        c.version AS claim_version FROM vote_receipts r
                    JOIN coverage_members m ON m.receipt_seq = r.seq
                    LEFT JOIN claims c ON c.family = r.family AND c.kind = 'repeat'
                        AND c.canonical_actor = (SELECT canonical_actor
                            FROM actor_resolution WHERE family = ? AND actor_id = ?)
                        AND c.comparison_key = CAST(r.seq AS TEXT)
                        AND c.merged_into IS NULL
                    WHERE r.family = ? AND r.actor_id = ? AND r.seq > ?
                        AND r.repeat_of IS NULL AND r.recorded_at <= ? - 86400000
                        AND (c.id IS NULL OR c.selected_receipt IS NULL)
                        AND NOT EXISTS (SELECT 1 FROM pair_leases l
                            WHERE l.repeat_of = r.seq AND l.status = 'pending'
                                AND l.expires_at > ?)
                    ORDER BY r.seq LIMIT 251
                    """,
                params=(family, actor_id, family, actor_id, after, now, now),
            )
        )
        selected = tuple(DueRepeat.model_validate(row) for row in rows[:_PAGE_ROWS])
        return RepeatPage(
            rows=selected,
            after=selected[-1].receipt_seq if len(rows) > _PAGE_ROWS else None,
        )
