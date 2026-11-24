import { ApiError } from './api';
import { first, onActivePair, queries } from './db';

export const PREVIEW_UNAVAILABLE = 'Preview unavailable. Reload the voting page to try again.';

export const PREVIEW_HEADERS = {
  'Cache-Control': 'private, no-store',
  'Content-Security-Policy':
    "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'; frame-src 'none'",
  'X-Frame-Options': 'SAMEORIGIN',
  'X-DNS-Prefetch-Control': 'off',
  'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff',
};

export async function preview(
  db: D1Database,
  actor: string,
  pair: string,
  side: 'a' | 'b',
): Promise<string> {
  const outputKey = side === 'a' ? 'pairs.a_output' : 'pairs.b_output';
  const row = await first(
    db,
    queries
      .selectFrom('pending_pairs as pairs')
      .innerJoin('active', onActivePair)
      .innerJoin('publications', (join) =>
        join
          .onRef('publications.id', '=', 'pairs.publication_id')
          .on('publications.render', '=', 'html'),
      )
      .innerJoin('outputs', (join) =>
        join
          .onRef('outputs.publication_id', '=', 'pairs.publication_id')
          .onRef('outputs.output_key', '=', outputKey),
      )
      .select('outputs.content')
      .where('pairs.id', '=', pair)
      .where('pairs.actor_id', '=', actor),
  );

  const content = row?.content ?? null;
  if (content === null) {
    throw new ApiError(404, 'not_found');
  }
  return content;
}
