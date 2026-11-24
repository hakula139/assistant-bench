import { batch, queries } from './db';
import { PAIR_RETENTION_MS, TAG_RETENTION_MS } from './limits';

/** Clears IP tags and deletes pending pairs past their retention. */
export async function retain(db: D1Database, now: number): Promise<void> {
  const tagged = now - TAG_RETENTION_MS;
  const expired = now - PAIR_RETENTION_MS;
  await batch(db, [
    queries
      .updateTable('votes')
      .set({ ip_tag: null })
      .where('ip_tag', 'is not', null)
      .where('recorded_at', '<=', tagged),
    queries
      .updateTable('actors')
      .set({ ip_tag: null })
      .where('ip_tag', 'is not', null)
      .where('created_at', '<=', tagged),
    queries.deleteFrom('pairs').where('issued_at', '<=', expired),
    queries.deleteFrom('batch_pairs').where('issued_at', '<=', expired),
  ]);
}
