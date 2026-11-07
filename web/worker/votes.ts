import type { Freshness, FreshnessQuery, TaggedDefects, VoteRequest } from '@/lib/arena';
import type { Defect } from '@/lib/defects';

import { ApiError, type Bindings } from './api';
import { FRESHNESS_CACHE_SECONDS, UNDO_MS, VOTES_PER_TAG, WINDOW_MS } from './limits';

/**
 * Every row present counts toward the tag's cap, so excluding a vote refunds nothing, while an
 * undone vote is gone and no longer counts.
 */
const TAG_VOTES = 'SELECT count(*) AS votes FROM votes WHERE ip_tag = ? AND recorded_at > ?';
const PRIOR = 'SELECT choice, defects FROM votes WHERE pair_id = ? AND actor_id = ?';
const PAIR_DEFECTS = `
  SELECT publications.defects FROM pairs
  JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  JOIN publications ON publications.id = pairs.publication_id
  WHERE pairs.id = ? AND pairs.actor_id = ?`;
const RECORD = `
  INSERT INTO votes (
    pair_id, actor_id, family, publication_id, case_key, a_output, b_output, a_system, b_system,
    choice, defects, elapsed_ms, recorded_at, ip_tag
  )
  SELECT pairs.id, pairs.actor_id, pairs.family, pairs.publication_id, pairs.case_key,
    pairs.a_output, pairs.b_output, a.system, b.system, ?1, ?2, max(0, ?3 - pairs.issued_at), ?3, ?4
  FROM pairs
  JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
  JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
  WHERE pairs.id = ?5 AND pairs.actor_id = ?6
    AND (SELECT count(*) FROM votes WHERE ip_tag = ?4 AND recorded_at > ?7) < ?8
  ON CONFLICT DO NOTHING RETURNING id`;
const RELEASE = `
  DELETE FROM pairs WHERE id = ?1 AND EXISTS (SELECT 1 FROM votes WHERE pair_id = ?1)`;
const RESTORE = `
  INSERT INTO pairs (id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at)
  SELECT pair_id, actor_id, family, publication_id, case_key, a_output, b_output,
    recorded_at - elapsed_ms
  FROM votes WHERE actor_id = ?1 AND family = ?2 AND recorded_at > ?3 AND exclusion IS NULL
  ORDER BY id DESC LIMIT 1
  ON CONFLICT (actor_id, family) DO UPDATE SET
    id = excluded.id, publication_id = excluded.publication_id, case_key = excluded.case_key,
    a_output = excluded.a_output, b_output = excluded.b_output, issued_at = excluded.issued_at`;
/** Deletes the vote whose pair `RESTORE` just reinstated, which it chose undoable and counted. */
const WITHDRAW = `
  DELETE FROM votes
  WHERE actor_id = ?1 AND family = ?2
    AND pair_id = (SELECT id FROM pairs WHERE actor_id = ?1 AND family = ?2)
  RETURNING id`;
const FRESHNESS = `
  SELECT EXISTS (SELECT 1 FROM votes WHERE family = ?1 AND exclusion IS NULL AND id > ?2)
    OR (SELECT coalesce(sum(votes), 0) FROM pair_vote_counts WHERE family = ?1) <> ?3
    AS stale`;

interface Prior {
  choice: string;
  defects: string;
}

/** Whether a stored vote holds the same choice and the same tags on each side. */
function same(prior: Prior, request: VoteRequest): boolean {
  const tags = JSON.parse(prior.defects) as TaggedDefects;
  const set = (items: readonly string[]) => JSON.stringify([...new Set(items)].sort());
  return (
    prior.choice === request.choice &&
    set(tags.a) === set(request.defects.a) &&
    set(tags.b) === set(request.defects.b)
  );
}

/** A vote's stored copy, its pair's defects while pending, and its tag's votes in the window. */
interface Found {
  prior: Prior | undefined;
  defects: string | undefined;
  votes: number;
}

async function find(
  db: D1Database,
  actor: string,
  tag: string,
  request: VoteRequest,
  now: number,
): Promise<Found> {
  const [priorRows, pairRows, tagRows] = await db.batch([
    db.prepare(PRIOR).bind(request.pair, actor),
    db.prepare(PAIR_DEFECTS).bind(request.pair, actor),
    db.prepare(TAG_VOTES).bind(tag, now - WINDOW_MS),
  ]);
  const [{ votes }] = tagRows.results as { votes: number }[];
  return {
    prior: (priorRows.results as Prior[]).at(0),
    defects: (pairRows.results as { defects: string }[]).at(0)?.defects,
    votes,
  };
}

/**
 * Decides a vote from what it finds: `null` when it repeats the stored vote, the tags to store in
 * the family's order when it may be recorded, and an `ApiError` otherwise.
 */
function decide(request: VoteRequest, { prior, defects, votes }: Found): TaggedDefects | null {
  if (prior !== undefined) {
    if (!same(prior, request)) {
      throw new ApiError(409, 'stale_pair');
    }
    return null;
  }
  if (defects === undefined) {
    throw new ApiError(409, 'stale_pair');
  }

  const allowed = JSON.parse(defects) as Defect[];
  const tagged = Object.values(request.defects).flat();
  if (tagged.some((defect) => !allowed.includes(defect))) {
    throw new ApiError(400, 'invalid_request');
  }
  if (votes >= VOTES_PER_TAG) {
    throw new ApiError(429, 'daily_limit');
  }
  return {
    a: allowed.filter((defect) => request.defects.a.includes(defect)),
    b: allowed.filter((defect) => request.defects.b.includes(defect)),
  };
}

/**
 * Records a vote on the actor's pending pair and releases the pair, in one transaction. Repeating
 * an identical request succeeds, so the browser can retry without knowing whether the first
 * attempt landed.
 */
export async function record(
  db: D1Database,
  actor: string,
  tag: string,
  request: VoteRequest,
  now: number,
): Promise<void> {
  const defects = decide(request, await find(db, actor, tag, request, now));
  if (defects === null) {
    return;
  }

  const [inserted] = await db.batch([
    db
      .prepare(RECORD)
      .bind(
        request.choice,
        JSON.stringify(defects),
        now,
        tag,
        request.pair,
        actor,
        now - WINDOW_MS,
        VOTES_PER_TAG,
      ),
    db.prepare(RELEASE).bind(request.pair),
  ]);
  if (inserted.results.length === 1) {
    return;
  }

  // A miss that still looks recordable met the actor's earlier vote on the same comparison.
  if (decide(request, await find(db, actor, tag, request, now)) !== null) {
    throw new ApiError(409, 'stale_pair');
  }
}

/**
 * Withdraws the actor's latest vote in the family while it is undoable, restoring its pair, with
 * the same ID and issue time, in place of the pending one. Each call walks one vote further back,
 * passing over votes the owner excluded, which stay recorded.
 */
export async function undo(db: D1Database, actor: string, family: string, now: number) {
  const since = now - UNDO_MS;
  const [, withdrawn] = await db.batch([
    db.prepare(RESTORE).bind(actor, family, since),
    db.prepare(WITHDRAW).bind(actor, family),
  ]);
  if (withdrawn.results.length === 0) {
    throw new ApiError(409, 'nothing_to_undo');
  }
}

/**
 * Whether this store's counted votes differ from the round's cut, including moderation changes.
 */
export async function freshness(
  db: D1Database,
  store: Bindings['STORE'],
  query: FreshnessQuery,
): Promise<Freshness> {
  if (store !== 'production' || query.production === undefined) {
    return 'unknown';
  }
  const row = await db
    .prepare(FRESHNESS)
    .bind(query.family, Number(query.production), Number(query.production_votes))
    .first<{ stale: number }>();
  return row?.stale ? 'stale' : 'current';
}

/**
 * Answers a freshness request from this data center's cache, reading the store at most once per
 * `FRESHNESS_CACHE_SECONDS` for each set of parameters, so busy boards cost no more rows.
 */
export async function cachedFreshness(
  url: string,
  answer: () => Promise<Freshness>,
): Promise<Freshness> {
  const key = new URL(url);
  key.searchParams.sort();
  const hit = await caches.default.match(key);
  if (hit !== undefined) {
    return (await hit.json<{ status: Freshness }>()).status;
  }
  const status = await answer();
  await caches.default.put(
    key,
    Response.json(
      { status },
      { headers: { 'Cache-Control': `max-age=${FRESHNESS_CACHE_SECONDS}` } },
    ),
  );
  return status;
}
