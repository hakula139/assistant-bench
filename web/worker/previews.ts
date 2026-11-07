import { ApiError } from './api';

export const previewUnavailable = 'Preview unavailable. Reload the voting page to try again.';

export const previewHeaders = {
  'Cache-Control': 'private, no-store',
  'Content-Security-Policy':
    "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'; frame-src 'none'",
  'X-Frame-Options': 'SAMEORIGIN',
  'X-DNS-Prefetch-Control': 'off',
  'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff',
};

/** Reads the selected HTML document only while this actor owns its active pending pair. */
export async function preview(
  db: D1Database,
  actor: string,
  pair: string,
  side: 'a' | 'b',
): Promise<string> {
  const row = await db
    .prepare(
      `SELECT outputs.content FROM pairs
       JOIN active ON active.family = pairs.family
         AND active.publication_id = pairs.publication_id
       JOIN publications ON publications.id = pairs.publication_id AND publications.render = 'html'
       JOIN outputs ON outputs.publication_id = pairs.publication_id
         AND outputs.output_key = CASE ? WHEN 'a' THEN pairs.a_output ELSE pairs.b_output END
       WHERE pairs.id = ? AND pairs.actor_id = ?`,
    )
    .bind(side, pair, actor)
    .first<{ content: string }>();
  if (row === null) {
    throw new ApiError(409, 'stale_pair');
  }
  return row.content;
}
