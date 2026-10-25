"""Bounded expiry work for private associations and unaccepted browsing state."""

from assistant_bench_arena.ledger_models import Statement
from assistant_bench_arena.transport import Transport


class LedgerMaintenance:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def expire(self, now: int) -> None:
        """Expire one indexed batch per table without changing numerical evidence."""
        statements = (
            Statement(
                sql="""
                    DELETE FROM network_attribution WHERE rowid IN (
                        SELECT rowid FROM network_attribution WHERE expires_at <= ?
                        ORDER BY expires_at LIMIT 250)
                    """,
                params=(now,),
            ),
            Statement(
                sql="""
                    DELETE FROM quota_windows WHERE rowid IN (
                        SELECT rowid FROM quota_windows WHERE expires_at <= ?
                        ORDER BY expires_at LIMIT 250)
                    """,
                params=(now,),
            ),
            Statement(
                sql="""
                    DELETE FROM admission_results WHERE command_id IN (
                        SELECT command_id FROM admission_results WHERE expires_at <= ?
                        ORDER BY expires_at LIMIT 250)
                    """,
                params=(now,),
            ),
            Statement(
                sql="""
                    DELETE FROM credentials WHERE id IN (
                        SELECT c.id FROM credentials c WHERE c.expires_at <= ?
                            AND NOT EXISTS (SELECT 1 FROM admission_results a
                                WHERE a.credential_id = c.id)
                            AND NOT EXISTS (SELECT 1 FROM admission_results a
                                WHERE a.original_credential = c.id)
                        ORDER BY c.expires_at LIMIT 250)
                    """,
                params=(now,),
            ),
            Statement(
                sql='INSERT INTO expire_leases(now_ms) VALUES (?)', params=(now,)
            ),
            Statement(
                sql="""
                    DELETE FROM pair_leases WHERE id IN (
                        SELECT id FROM pair_leases WHERE expires_at <= ? - 604800000
                            AND status != 'pending' ORDER BY expires_at, id LIMIT 250)
                    """,
                params=(now,),
            ),
            Statement(
                sql="""
                    DELETE FROM claims WHERE id IN (
                        SELECT e.claim_id FROM claim_expiry e
                        WHERE e.expires_at <= ? AND NOT EXISTS (
                            SELECT 1 FROM pair_leases l WHERE l.claim_id = e.claim_id)
                        ORDER BY e.expires_at, e.claim_id LIMIT 250)
                    """,
                params=(now,),
            ),
            Statement(
                sql="""
                    DELETE FROM sessions WHERE id IN (
                        SELECT e.session_id FROM session_expiry e
                        WHERE e.expires_at <= ?
                            AND NOT EXISTS (SELECT 1 FROM pair_leases l
                                WHERE l.session_id = e.session_id)
                        ORDER BY e.expires_at, e.session_id LIMIT 250)
                    """,
                params=(now,),
            ),
            Statement(
                sql="""
                    DELETE FROM actors WHERE id IN (
                        SELECT e.actor_id FROM actor_expiry e
                        WHERE e.expires_at <= ?
                            AND NOT EXISTS (SELECT 1 FROM sessions s
                                WHERE s.actor_id = e.actor_id)
                            AND NOT EXISTS (SELECT 1 FROM credentials c
                                WHERE c.actor_id = e.actor_id)
                            AND NOT EXISTS (SELECT 1 FROM claims c
                                WHERE c.actor_id = e.actor_id)
                            AND NOT EXISTS (SELECT 1 FROM admission_results a
                                WHERE a.actor_id = e.actor_id)
                        ORDER BY e.expires_at, e.actor_id LIMIT 250)
                    """,
                params=(now,),
            ),
        )

        for statement in statements:
            await self.transport.execute(statement)

    async def attribution_lag(self, now: int) -> int:
        """Return milliseconds past the oldest pending association expiry."""
        rows = await self.transport.query(
            Statement(sql='SELECT min(expires_at) AS oldest FROM network_attribution')
        )
        oldest = rows[0]['oldest']

        if oldest is None:
            return 0

        if not isinstance(oldest, int):
            raise TypeError('An attribution expiry must be an integer timestamp.')

        return max(0, now - oldest)
