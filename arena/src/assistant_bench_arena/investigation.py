"""Private receipt investigation, separate from permanent numerical exports."""

from typing import Literal

from pydantic import NonNegativeInt

from assistant_bench_arena.ledger_models import (
    Attribution,
    Identifier,
    Observation,
    SqlValue,
    Statement,
)
from assistant_bench_arena.models import Record
from assistant_bench_arena.transport import Transport


class NetworkLookup(Record):
    version: Identifier
    kind: Literal['exact', 'prefix']
    tag: Identifier


class ReceiptFilter(Record):
    family: Identifier
    actor_id: Identifier | None = None
    session_id: Identifier | None = None
    evaluation_id: Identifier | None = None
    """Served evaluation or the legacy observation's proof evaluation."""

    recorded_from: NonNegativeInt | None = None
    recorded_before: NonNegativeInt | None = None
    status: Literal['any', 'active', 'withdrawn', 'excluded', 'superseded'] = 'any'
    network: NetworkLookup | None = None


class InvestigatedReceipt(Record):
    observation: Observation
    canonical_actor: str
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


class LedgerInvestigation:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def receipts(
        self, filters: ReceiptFilter, now: int, after: int = 0
    ) -> InvestigationPage:
        """Read at most 75 receipts and 1 MiB, with expired network links excluded."""
        predicates = ['r.family = ?', 'r.seq > ?']
        params: list[SqlValue] = [now, filters.family, after]

        for column, value in (
            ('r.actor_id', filters.actor_id),
            ('r.session_id', filters.session_id),
            ('coalesce(r.evaluation_id, r.proof_evaluation_id)', filters.evaluation_id),
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
                'canonical_actor', a.canonical_actor, 'claim_version', c.version,
                'selected', json(CASE WHEN c.selected_receipt = r.seq
                    AND c.merged_into IS NULL THEN 'true' ELSE 'false' END),
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
            JOIN actor_resolution a ON a.actor_id = r.actor_id AND a.family = r.family
            WHERE {where}
            """
        rows = await self.transport.query(
            Statement(
                sql=f"""
                    WITH candidates AS ({query} ORDER BY r.seq LIMIT 76),
                    sized AS (
                        SELECT seq, body, sum(length(CAST(body AS BLOB)))
                            OVER (ORDER BY seq) AS bytes FROM candidates)
                    SELECT seq, CASE WHEN bytes <= 1048576 THEN body END AS body
                    FROM sized ORDER BY seq
                    """,
                params=tuple(params),
            )
        )
        decoded: list[InvestigatedReceipt] = []
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

            decoded.append(InvestigatedReceipt.model_validate_json(body))
            through = seq

        return InvestigationPage(
            rows=tuple(decoded),
            after=through if len(rows) > len(decoded) else None,
        )
