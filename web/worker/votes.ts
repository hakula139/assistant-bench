import type {
  Freshness,
  FreshnessQuery,
  TaggedDefects,
  UndoRequest,
  VoteRequest,
} from '@/lib/arena';
import type { Defect } from '@/lib/defects';

import { ApiError, type Bindings } from './api';
import { FRESHNESS_CACHE_SECONDS, UNDO_MS, VOTES_PER_TAG, WINDOW_MS } from './limits';

const STATE_FIELDS = 'prior.choice, prior.defects AS saved, publications.defects AS allowed';
const STATE_JOINS = `
  LEFT JOIN votes AS prior ON prior.pair_id = ?1 AND prior.actor_id = ?2
  LEFT JOIN pairs ON pairs.id = ?1 AND pairs.actor_id = ?2
  LEFT JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  LEFT JOIN publications ON publications.id = active.publication_id`;
const FIND = `SELECT ${STATE_FIELDS}, 0 AS capped FROM (SELECT 1) ${STATE_JOINS}`;
const HAS_ROOM = '(SELECT count(*) FROM votes WHERE ip_tag = ?3 AND recorded_at > ?4) < ?5';
/** Classifies a miss in the insert's snapshot before another vote or undo can change the result. */
const CHECK_MISS = `
  SELECT ${STATE_FIELDS}, NOT (${HAS_ROOM}) AS capped
  FROM (SELECT 1 WHERE changes() = 0)
  ${STATE_JOINS}`;
const RECORD = `
  INSERT INTO votes (
    pair_id, actor_id, family, publication_id, case_key, a_output, b_output, a_system, b_system,
    choice, defects, elapsed_ms, recorded_at, ip_tag
  )
  SELECT pairs.id, pairs.actor_id, pairs.family, pairs.publication_id, pairs.case_key,
    pairs.a_output, pairs.b_output, a.system, b.system, ?6, ?7, max(0, ?8 - pairs.issued_at), ?8, ?3
  FROM pairs
  JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
  JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
  WHERE pairs.id = ?1 AND pairs.actor_id = ?2 AND ${HAS_ROOM}
  ON CONFLICT DO NOTHING RETURNING id`;
const RELEASE = `
  DELETE FROM pairs WHERE id = ?1 AND EXISTS (SELECT 1 FROM votes WHERE pair_id = ?1)`;
const RESTORE = `
  INSERT INTO pairs (id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at)
  SELECT ?5, actor_id, family, publication_id, case_key, a_output, b_output,
    recorded_at - elapsed_ms
  FROM (
    SELECT * FROM votes
    WHERE actor_id = ?1 AND family = ?2 AND recorded_at > ?3 AND exclusion IS NULL
    ORDER BY id DESC LIMIT 1
  )
  WHERE pair_id = ?4
  ON CONFLICT (actor_id, family) DO UPDATE SET
    id = excluded.id, publication_id = excluded.publication_id, case_key = excluded.case_key,
    a_output = excluded.a_output, b_output = excluded.b_output, issued_at = excluded.issued_at`;
const WITHDRAW = `
  DELETE FROM votes
  WHERE actor_id = ?1 AND family = ?2 AND pair_id = ?3 AND changes() = 1
  RETURNING id`;
const FRESHNESS = `
  SELECT EXISTS (SELECT 1 FROM votes WHERE family = ?1 AND exclusion IS NULL AND id > ?2)
    OR (SELECT coalesce(sum(votes), 0) FROM pair_vote_counts WHERE family = ?1) <> ?3
    AS stale`;

interface Found {
  choice: string | null;
  saved: string | null;
  allowed: string | null;
  capped: 0 | 1;
}

/** Whether a stored vote holds the same choice and the same tags on each side. */
function same(choice: string, saved: string, request: VoteRequest): boolean {
  const tags = JSON.parse(saved) as TaggedDefects;
  const set = (items: readonly string[]) => JSON.stringify([...new Set(items)].sort());
  return (
    choice === request.choice &&
    set(tags.a) === set(request.defects.a) &&
    set(tags.b) === set(request.defects.b)
  );
}

/**
 * Decides a vote from what it finds: `null` when it repeats the stored vote, the tags to store in
 * the family's order when it may be recorded, and an `ApiError` otherwise.
 */
function decide(request: VoteRequest, found: Found | null): TaggedDefects | null {
  if (found === null) {
    throw new ApiError(409, 'stale_pair');
  }
  if (found.choice !== null && found.saved !== null) {
    if (!same(found.choice, found.saved, request)) {
      throw new ApiError(409, 'stale_pair');
    }
    return null;
  }
  if (found.allowed === null) {
    throw new ApiError(409, 'stale_pair');
  }

  const allowed = JSON.parse(found.allowed) as Defect[];
  const tagged = Object.values(request.defects).flat();
  if (tagged.some((defect) => !allowed.includes(defect))) {
    throw new ApiError(400, 'invalid_request');
  }
  if (found.capped) {
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
  const found = await db.prepare(FIND).bind(request.pair, actor).first<Found>();
  const defects = decide(request, found);
  if (defects === null) {
    return;
  }

  const scope = [request.pair, actor, tag, now - WINDOW_MS, VOTES_PER_TAG] as const;
  const [inserted, missed] = await db.batch([
    db.prepare(RECORD).bind(...scope, request.choice, JSON.stringify(defects), now),
    db.prepare(CHECK_MISS).bind(...scope),
    db.prepare(RELEASE).bind(request.pair),
  ]);
  if (inserted.results.length === 1) {
    return;
  }

  if (decide(request, (missed.results as Found[]).at(0) ?? null) !== null) {
    // A miss that still looks recordable met the actor's earlier vote on the same comparison.
    throw new ApiError(409, 'stale_pair');
  }
}

/** Confirms an exact stored vote without changing a vote or its pending pair. */
export async function confirm(db: D1Database, actor: string, request: VoteRequest) {
  const saved = await db
    .prepare('SELECT choice, defects FROM votes WHERE pair_id = ? AND actor_id = ?')
    .bind(request.pair, actor)
    .first<{ choice: string; defects: string }>();
  if (saved === null || !same(saved.choice, saved.defects, request)) {
    throw new ApiError(409, 'stale_pair');
  }
}

/**
 * Withdraws the specified vote while it is the latest undoable vote in the family. The restored
 * pair keeps its issue time and gets a fresh ID so the withdrawn vote cannot be replayed.
 */
export async function undo(db: D1Database, actor: string, request: UndoRequest, now: number) {
  const { family, pair } = request;
  const restored = crypto.randomUUID();
  const [, withdrawn] = await db.batch([
    db.prepare(RESTORE).bind(actor, family, now - UNDO_MS, pair, restored),
    db.prepare(WITHDRAW).bind(actor, family, pair),
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
