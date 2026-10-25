"""Private receipt investigation, separate from permanent numerical exports."""

from pydantic import BaseModel

from assistant_bench_arena.investigation_models import (
    FamilyStatus,
    InvestigatedReceipt,
    InvestigationPage,
    ReceiptFilter,
)
from assistant_bench_arena.ledger_models import SqlValue, Statement
from assistant_bench_arena.transport import Transport


class LedgerInvestigation:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def status(self, family: str) -> FamilyStatus | None:
        """Read current control, sampling provenance and raw coverage together."""
        rows = await self.transport.query(
            Statement(
                sql="""
                    SELECT json_object('state', json_object('family', f.family,
                        'evaluation_id', f.evaluation_id,
                        'publication_id', f.publication_id,
                        'point_id', f.point_id, 'policy_id', f.policy_id,
                        'last_evidence_seq', f.last_evidence_seq,
                        'revision', f.revision),
                        'sampling_source', CASE WHEN x.id IS NOT NULL THEN
                            json_object('export_id', x.id,
                                'analysis_sequence', x.analysis_sequence,
                                'event_cutoff', x.event_cutoff,
                                'receipt_cutoff', x.receipt_cutoff) END,
                        'event_cutoff', coalesce((SELECT max(seq) FROM evidence_events
                            WHERE family = f.family), 0),
                        'receipt_cutoff', coalesce((SELECT max(seq) FROM vote_receipts
                            WHERE family = f.family), 0),
                        'eligible_votes', coalesce((SELECT sum(count) FROM coverage
                            WHERE family = f.family), 0)) AS body
                    FROM family_state f JOIN point_snapshots p ON p.id = f.point_id
                    LEFT JOIN analysis_exports x ON x.id = p.source_export_id
                    WHERE f.family = ?
                    """,
                params=(family,),
            )
        )

        if not rows:
            return None

        body = rows[0]['body']

        if not isinstance(body, str):
            raise TypeError('A family status must contain a JSON body.')

        return FamilyStatus.model_validate_json(body)

    async def _page[T: BaseModel](
        self, query: str, params: tuple[SqlValue, ...], model: type[T]
    ) -> tuple[tuple[T, ...], int | None]:
        rows = await self.transport.query(
            Statement(
                sql=f"""
                    WITH candidates AS ({query} LIMIT 76),
                    sized AS (
                        SELECT seq, body, sum(length(CAST(body AS BLOB)))
                            OVER (ORDER BY seq) AS bytes FROM candidates)
                    SELECT seq, CASE WHEN bytes <= 1048576 THEN body END AS body
                    FROM sized ORDER BY seq
                    """,
                params=params,
            )
        )
        decoded: list[T] = []
        through: int | None = None

        for row in rows[:75]:
            body = row['body']
            seq = row['seq']

            if body is None:
                if not decoded:
                    raise ValueError(
                        'One investigation record exceeds the transport limit.'
                    )

                break

            if not isinstance(body, str) or not isinstance(seq, int):
                raise TypeError(
                    'An investigation record must have JSON and a sequence.'
                )

            decoded.append(model.model_validate_json(body))
            through = seq

        return tuple(decoded), through if len(rows) > len(decoded) else None

    async def receipts(
        self, filters: ReceiptFilter, now: int, after: int = 0
    ) -> InvestigationPage:
        """Read at most 75 receipts and 1 MiB, with expired network links excluded."""
        predicates = ['r.family = ?', 'r.seq > ?']
        params: list[SqlValue] = [now, filters.family, after]

        for column, value in (
            ('r.actor_id', filters.actor_id),
            ('r.session_id', filters.session_id),
            ('r.evaluation_id', filters.evaluation_id),
        ):
            if value is not None:
                predicates.append(f'{column} = ?')
                params.append(value)

        for column, operator, timestamp in (
            ('r.recorded_at', '>=', filters.recorded_from),
            ('r.recorded_at', '<', filters.recorded_before),
        ):
            if timestamp is not None:
                predicates.append(f'{column} {operator} ?')
                params.append(timestamp)

        if filters.status == 'active':
            predicates.append(
                's.withdrawn IS NULL AND s.excluded IS NULL AND s.superseded IS NULL'
            )
        elif filters.status != 'any':
            predicates.append(f's.{filters.status} IS NOT NULL')

        if filters.network is not None:
            predicates.append(
                """
                EXISTS (SELECT 1 FROM network_attribution n WHERE n.receipt_seq = r.seq
                    AND n.key_version = ? AND n.tag_kind = ? AND n.tag = ?
                    AND n.expires_at > ?)
                """
            )
            params.extend(
                (
                    filters.network.version,
                    filters.network.kind,
                    filters.network.tag,
                    now,
                )
            )

        where = ' AND '.join(predicates)
        query = f"""
            SELECT r.seq, json_object('observation', json(o.body),
                'claim_version', c.version,
                'selected', json(CASE WHEN c.selected_receipt = r.seq
                    THEN 'true' ELSE 'false' END),
                'eligible', json(CASE WHEN EXISTS (SELECT 1 FROM coverage_members m
                    WHERE m.receipt_seq = r.seq) THEN 'true' ELSE 'false' END),
                'withdrawn', s.withdrawn, 'excluded', s.excluded,
                'superseded', s.superseded,
                'network', json((SELECT json_group_array(json_object(
                    'version', n.key_version, 'kind', n.tag_kind,
                    'tag', n.tag, 'expires_at', n.expires_at))
                    FROM network_attribution n WHERE n.receipt_seq = r.seq
                        AND n.expires_at > ?))) AS body
            FROM vote_receipts r JOIN observation_bodies o ON o.seq = r.seq
            JOIN receipt_state s ON s.receipt_seq = r.seq
            JOIN claims c ON c.id = r.claim_id
            WHERE {where} ORDER BY r.seq
            """
        decoded, through = await self._page(query, tuple(params), InvestigatedReceipt)

        return InvestigationPage(
            rows=decoded,
            after=through,
        )
