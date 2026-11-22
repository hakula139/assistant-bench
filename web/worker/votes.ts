import type { TaggedDefects, UndoRequest, VoteRequest, VoteStatus } from '@/lib/arena';
import type { Defect } from '@/lib/defects';

import { ApiError } from './api';
import { UNDO_MS } from './limits';

const FIND = `
  SELECT prior.choice, prior.defects AS saved, publications.defects AS allowed,
    coalesce(prior.family, pairs.family) AS family
  FROM (SELECT 1)
  LEFT JOIN votes AS prior ON prior.pair_id = ?1 AND prior.actor_id = ?2
  LEFT JOIN pending_pairs AS pairs ON pairs.id = ?1 AND pairs.actor_id = ?2
  LEFT JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  LEFT JOIN publications ON publications.id = active.publication_id`;
const RECEIPT = 'SELECT choice, defects AS saved FROM votes WHERE pair_id = ?1 AND actor_id = ?2';
/** Reads the receipt for a missed write before another submission or undo can change it. */
const CHECK_MISS = `${RECEIPT} AND changes() = 0`;
const RECORD = `
  INSERT INTO votes (
    pair_id, actor_id, family, publication_id, case_key, a_output, b_output, a_system, b_system,
    choice, defects, elapsed_ms, recorded_at, ip_tag
  )
  SELECT pairs.id, pairs.actor_id, pairs.family, pairs.publication_id, pairs.case_key,
    pairs.a_output, pairs.b_output, a.system, b.system, ?4, ?5, max(0, ?6 - pairs.issued_at), ?6, ?3
  FROM pending_pairs AS pairs
  JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
  JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
  WHERE pairs.id = ?1 AND pairs.actor_id = ?2
  ON CONFLICT DO NOTHING RETURNING id`;
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

interface Found {
  choice: string | null;
  saved: string | null;
  allowed: string | null;
  family: string | null;
}

interface Receipt {
  choice: string;
  saved: string;
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

function receiptStatus(request: VoteRequest, receipt: Receipt | null): VoteStatus {
  return receipt !== null && same(receipt.choice, receipt.saved, request)
    ? 'identical'
    : 'stale_pair';
}

/** Supplies the publication's ordered tags for a new vote, or resolves the request immediately. */
function decide(
  request: VoteRequest,
  found: Found | undefined,
  family: string | undefined,
): TaggedDefects | VoteStatus {
  if (found === undefined || (family !== undefined && found.family !== family)) {
    return 'stale_pair';
  }
  if (found.choice !== null && found.saved !== null) {
    return receiptStatus(request, { choice: found.choice, saved: found.saved });
  }
  if (found.allowed === null) {
    return 'stale_pair';
  }

  const allowed = JSON.parse(found.allowed) as Defect[];
  const tagged = Object.values(request.defects).flat();
  if (tagged.some((defect) => !allowed.includes(defect))) {
    return 'invalid_request';
  }
  return {
    a: allowed.filter((defect) => request.defects.a.includes(defect)),
    b: allowed.filter((defect) => request.defects.b.includes(defect)),
  };
}

function write(
  db: D1Database,
  actor: string,
  tag: string,
  request: VoteRequest,
  defects: TaggedDefects,
  now: number,
) {
  return [
    db.prepare(RECORD).bind(request.pair, actor, tag, request.choice, JSON.stringify(defects), now),
    db.prepare(CHECK_MISS).bind(request.pair, actor),
  ];
}

/** Classifies all receipts in input order and writes eligible new votes with one timestamp. */
export async function recordBatch(
  db: D1Database,
  actor: string,
  tag: string,
  requests: readonly VoteRequest[],
  now: number,
  family?: string,
) {
  const found = await db.batch<Found>(
    requests.map(({ pair }) => db.prepare(FIND).bind(pair, actor)),
  );
  const statuses: VoteStatus[] = [];
  const writes: D1PreparedStatement[] = [];
  const offsets: { index: number; offset: number }[] = [];
  for (const [index, request] of requests.entries()) {
    const decision = decide(request, found[index].results.at(0), family);
    if (typeof decision === 'string') {
      statuses[index] = decision;
    } else {
      offsets.push({ index, offset: writes.length });
      writes.push(...write(db, actor, tag, request, decision, now));
    }
  }
  if (writes.length > 0) {
    const results = await db.batch(writes);
    for (const { index, offset } of offsets) {
      statuses[index] =
        results[offset].results.length > 0
          ? 'recorded'
          : receiptStatus(
              requests[index],
              (results[offset + 1].results as Receipt[]).at(0) ?? null,
            );
    }
  }
  return requests.map(({ pair }, index) => ({ pair, status: statuses[index] }));
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
  const [{ status }] = await recordBatch(db, actor, tag, [request], now);
  if (status === 'stale_pair' || status === 'invalid_request') {
    throw new ApiError(status === 'stale_pair' ? 409 : 400, status);
  }
}

/** Confirms an exact stored vote without changing a vote or its pending pair. */
export async function confirm(db: D1Database, actor: string, request: VoteRequest) {
  const receipt = await db.prepare(RECEIPT).bind(request.pair, actor).first<Receipt>();
  if (receiptStatus(request, receipt) === 'stale_pair') {
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
