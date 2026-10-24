"""Private receipt investigation, separate from permanent numerical exports."""

from typing import Literal

from pydantic import BaseModel, NonNegativeInt

from assistant_bench_arena.ledger_models import (
    Attribution,
    Identifier,
    ImportRow,
    ImportSource,
    Observation,
    SqlValue,
    Statement,
)
from assistant_bench_arena.models import Record
from assistant_bench_arena.read_models import FamilyState
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


class StoredImportSource(Record):
    source: ImportSource
    completed_at: NonNegativeInt | None

    sessions: NonNegativeInt
    receipts: NonNegativeInt
    withdrawals: NonNegativeInt
    """Rows already preserved, independently of the declared snapshot totals."""


class StoredImportRow(Record):
    seq: NonNegativeInt
    source_id: str
    row: ImportRow


class ImportRowPage(Record):
    rows: tuple[StoredImportRow, ...]
    after: NonNegativeInt | None


class SamplingSource(Record):
    export_id: str
    analysis_sequence: NonNegativeInt
    event_cutoff: NonNegativeInt
    receipt_cutoff: NonNegativeInt
    import_cutoff: NonNegativeInt
    import_source_cutoff: NonNegativeInt


class FamilyStatus(Record):
    state: FamilyState
    sampling_source: SamplingSource | None

    event_cutoff: NonNegativeInt
    receipt_cutoff: NonNegativeInt
    """Latest family-local immutable sequences at this read snapshot."""

    eligible_votes: NonNegativeInt
    """Raw selected ordinary coverage, before offline actor weighting."""


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
                                'receipt_cutoff', x.receipt_cutoff,
                                'import_cutoff', x.import_cutoff,
                                'import_source_cutoff', x.import_source_cutoff) END,
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

    async def import_source(self, source_id: str) -> StoredImportSource | None:
        rows = await self.transport.query(
            Statement(
                sql="""
                    SELECT json_object('source', json_object('id', s.id,
                        'family', s.family, 'owner_id', s.owner_id,
                        'source_schema', s.source_schema, 'file_digest', s.file_digest,
                        'sessions', s.session_count, 'receipts', s.receipt_count,
                        'withdrawals', s.withdrawal_count),
                        'completed_at', s.completed_at,
                        'sessions', (SELECT count(*) FROM import_rows r
                            WHERE r.source_id = s.id AND r.row_kind = 'session'),
                        'receipts', (SELECT count(*) FROM import_rows r
                            WHERE r.source_id = s.id AND r.row_kind = 'receipt'),
                        'withdrawals', (SELECT count(*) FROM import_rows r
                            WHERE r.source_id = s.id AND r.row_kind = 'withdrawal'))
                        AS body FROM import_sources s WHERE s.id = ?
                    """,
                params=(source_id,),
            )
        )

        if not rows:
            return None

        body = rows[0]['body']

        if not isinstance(body, str):
            raise TypeError('An import source must contain a JSON body.')

        return StoredImportSource.model_validate_json(body)

    async def import_rows(self, source_id: str, after: int = 0) -> ImportRowPage:
        """Read at most 75 preserved rows and 1 MiB, excluding private notes."""
        rows, cursor = await self._page(
            """
            SELECT seq, json_object('seq', seq, 'source_id', source_id,
                'row', json_object('kind', row_kind, 'original_id', original_id,
                    'source_hash', row_hash, 'raw', json(raw),
                    'initial_disposition', initial_disposition)) AS body
            FROM import_rows WHERE source_id = ? AND seq > ? ORDER BY seq
            """,
            (source_id, after),
            StoredImportRow,
        )

        return ImportRowPage(rows=rows, after=cursor)

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
            WHERE {where} ORDER BY r.seq
            """
        decoded, through = await self._page(query, tuple(params), InvestigatedReceipt)

        return InvestigationPage(
            rows=decoded,
            after=through,
        )
