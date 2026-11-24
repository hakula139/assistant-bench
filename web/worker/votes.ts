import { sql, type Compilable, type InferResult } from 'kysely';

import type { TaggedDefects, UndoRequest, VoteRequest, VoteStatus } from '@/lib/arena';
import type { Defect } from '@/lib/defects';

import { ApiError } from './api';
import { batch, first, queries } from './db';
import { UNDO_MS } from './limits';

function findQuery(pair: string, actor: string) {
  return queries
    .selectFrom(queries.selectNoFrom(sql<1>`1`.as('probe')).as('probe'))
    .leftJoin('votes as prior', (join) =>
      join.on('prior.pair_id', '=', pair).on('prior.actor_id', '=', actor),
    )
    .leftJoin('pending_pairs as pairs', (join) =>
      join.on('pairs.id', '=', pair).on('pairs.actor_id', '=', actor),
    )
    .leftJoin('active', (join) =>
      join
        .onRef('active.family', '=', 'pairs.family')
        .onRef('active.publication_id', '=', 'pairs.publication_id'),
    )
    .leftJoin('publications', 'publications.id', 'active.publication_id')
    .select((eb) => [
      'prior.choice',
      'prior.defects as saved',
      'publications.defects as allowed',
      eb.fn.coalesce('prior.family', 'pairs.family').as('family'),
    ]);
}

function receiptQuery(pair: string, actor: string) {
  return queries
    .selectFrom('votes')
    .select(['choice', 'defects as saved'])
    .where('pair_id', '=', pair)
    .where('actor_id', '=', actor);
}

/** Reads the receipt for a missed write before another submission or undo can change it. */
function checkMissQuery(pair: string, actor: string) {
  return receiptQuery(pair, actor).where(sql<number>`changes()`, '=', 0);
}

function recordQuery(
  actor: string,
  tag: string,
  request: VoteRequest,
  defects: TaggedDefects,
  now: number,
) {
  return queries
    .insertInto('votes')
    .columns([
      'pair_id',
      'actor_id',
      'family',
      'publication_id',
      'case_key',
      'a_output',
      'b_output',
      'a_system',
      'b_system',
      'choice',
      'defects',
      'elapsed_ms',
      'recorded_at',
      'ip_tag',
    ])
    .expression((eb) =>
      eb
        .selectFrom('pending_pairs as pairs')
        .innerJoin('active', (join) =>
          join
            .onRef('active.family', '=', 'pairs.family')
            .onRef('active.publication_id', '=', 'pairs.publication_id'),
        )
        .innerJoin('outputs as a', (join) =>
          join
            .onRef('a.publication_id', '=', 'pairs.publication_id')
            .onRef('a.output_key', '=', 'pairs.a_output'),
        )
        .innerJoin('outputs as b', (join) =>
          join
            .onRef('b.publication_id', '=', 'pairs.publication_id')
            .onRef('b.output_key', '=', 'pairs.b_output'),
        )
        .select((sub) => [
          'pairs.id',
          'pairs.actor_id',
          'pairs.family',
          'pairs.publication_id',
          'pairs.case_key',
          'pairs.a_output',
          'pairs.b_output',
          'a.system as a_system',
          'b.system as b_system',
          sub.val(request.choice).as('choice'),
          sub.val(JSON.stringify(defects)).as('defects'),
          sql<number>`max(0, ${now} - ${sub.ref('pairs.issued_at')})`.as('elapsed_ms'),
          sub.val(now).as('recorded_at'),
          sub.val(tag).as('ip_tag'),
        ])
        .where('pairs.id', '=', request.pair)
        .where('pairs.actor_id', '=', actor),
    )
    .onConflict((oc) => oc.doNothing())
    .returning('id');
}

function restoreQuery(actor: string, family: string, pair: string, restored: string, now: number) {
  return queries
    .insertInto('pairs')
    .columns([
      'id',
      'actor_id',
      'family',
      'publication_id',
      'case_key',
      'a_output',
      'b_output',
      'issued_at',
    ])
    .expression((eb) =>
      eb
        .selectFrom(
          eb
            .selectFrom('votes')
            .selectAll()
            .where('actor_id', '=', actor)
            .where('family', '=', family)
            .where('recorded_at', '>', now - UNDO_MS)
            .where('exclusion', 'is', null)
            .orderBy('id', 'desc')
            .limit(1)
            .as('latest'),
        )
        .select((sub) => [
          sub.val(restored).as('id'),
          'latest.actor_id',
          'latest.family',
          'latest.publication_id',
          'latest.case_key',
          'latest.a_output',
          'latest.b_output',
          sql<number>`${sub.ref('latest.recorded_at')} - ${sub.ref('latest.elapsed_ms')}`.as(
            'issued_at',
          ),
        ])
        .where('latest.pair_id', '=', pair),
    )
    .onConflict((oc) =>
      oc.columns(['actor_id', 'family']).doUpdateSet((eb) => ({
        id: eb.ref('excluded.id'),
        publication_id: eb.ref('excluded.publication_id'),
        case_key: eb.ref('excluded.case_key'),
        a_output: eb.ref('excluded.a_output'),
        b_output: eb.ref('excluded.b_output'),
        issued_at: eb.ref('excluded.issued_at'),
      })),
    );
}

function withdrawQuery(actor: string, family: string, pair: string) {
  return queries
    .deleteFrom('votes')
    .where('actor_id', '=', actor)
    .where('family', '=', family)
    .where('pair_id', '=', pair)
    .where(sql<number>`changes()`, '=', 1)
    .returning('id');
}

type FoundRow = InferResult<ReturnType<typeof findQuery>>[number];
type ReceiptRow = InferResult<ReturnType<typeof receiptQuery>>[number];
type WriteRow = InferResult<ReturnType<typeof recordQuery>>[number] | ReceiptRow;

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

function receiptStatus(request: VoteRequest, receipt: ReceiptRow | null): VoteStatus {
  return receipt !== null && same(receipt.choice, receipt.saved, request)
    ? 'identical'
    : 'stale_pair';
}

/** Supplies the publication's ordered tags for a new vote, or resolves the request immediately. */
function decide(
  request: VoteRequest,
  found: FoundRow | undefined,
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

/** Classifies all receipts in input order and writes eligible new votes with one timestamp. */
export async function recordBatch(
  db: D1Database,
  actor: string,
  tag: string,
  requests: readonly VoteRequest[],
  now: number,
  family?: string,
) {
  const found = await batch(
    db,
    requests.map(({ pair }) => findQuery(pair, actor)),
  );
  const statuses: VoteStatus[] = [];
  const writes: Compilable<WriteRow>[] = [];
  const offsets: { index: number; offset: number }[] = [];
  for (const [index, request] of requests.entries()) {
    const decision = decide(request, found[index].results.at(0), family);
    if (typeof decision === 'string') {
      statuses[index] = decision;
    } else {
      offsets.push({ index, offset: writes.length });
      writes.push(
        recordQuery(actor, tag, request, decision, now),
        checkMissQuery(request.pair, actor),
      );
    }
  }
  const results = await batch(db, writes);
  for (const { index, offset } of offsets) {
    const miss = results[offset + 1].results.at(0);
    const receipt = miss !== undefined && 'choice' in miss ? miss : null;
    statuses[index] =
      results[offset].results.length > 0 ? 'recorded' : receiptStatus(requests[index], receipt);
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
  const receipt = await first(db, receiptQuery(request.pair, actor));
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
  const [, withdrawn] = await batch(db, [
    restoreQuery(actor, family, pair, restored, now),
    withdrawQuery(actor, family, pair),
  ]);
  if (withdrawn.results.length === 0) {
    throw new ApiError(409, 'nothing_to_undo');
  }
}
