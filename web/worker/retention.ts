import { PAIR_RETENTION_MS, TAG_RETENTION_MS } from './limits';

/** Clears IP tags past their retention and deletes pending pairs left idle too long. */
export async function retain(db: D1Database, now: number): Promise<void> {
  const tagged = now - TAG_RETENTION_MS;
  await db.batch([
    db
      .prepare('UPDATE votes SET ip_tag = NULL WHERE ip_tag IS NOT NULL AND recorded_at <= ?')
      .bind(tagged),
    db
      .prepare('UPDATE actors SET ip_tag = NULL WHERE ip_tag IS NOT NULL AND created_at <= ?')
      .bind(tagged),
    db.prepare('DELETE FROM pairs WHERE issued_at <= ?').bind(now - PAIR_RETENTION_MS),
  ]);
}
