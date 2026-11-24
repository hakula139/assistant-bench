import { expressionBuilder, sql, type InferResult } from 'kysely';

import type { PairState, PairView } from '@/lib/arena';
import type { Defect } from '@/lib/defects';

import { ApiError } from './api';
import { all, batch, first, queries } from './db';
import { hex, signer } from './hmac';
import { UNDO_MS } from './limits';
import type { Database } from './schema';

type SystemPair = readonly [string, string];

interface Comparison {
  caseKey: string;
  systems: SystemPair;
}

interface CandidateGroup {
  systems: SystemPair;
  caseKeys: readonly string[];
}

/** A case and the outputs shown as A and B. */
interface Drawn {
  caseKey: string;
  a: string;
  b: string;
}

/** Ranks items in one voter's fixed private order, as hex digests that compare as strings. */
export type Rank = (...parts: string[]) => Promise<string>;

function activeQuery(family: string) {
  return queries
    .selectFrom('active')
    .innerJoin('publications', 'publications.id', 'active.publication_id')
    .select((eb) => [
      'publications.id',
      'publications.render',
      'publications.defects',
      eb.fn
        .coalesce(
          eb.selectFrom('votes').select((sub) => sub.fn.max<number | null>('id').as('id')),
          eb.val(0),
        )
        .as('vote_cut'),
    ])
    .where('active.family', '=', family);
}

function pendingQuery(actor: string, family: string) {
  return queries
    .selectFrom('pending_pairs as pairs')
    .innerJoin('cases', (join) =>
      join
        .onRef('cases.publication_id', '=', 'pairs.publication_id')
        .onRef('cases.case_key', '=', 'pairs.case_key'),
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
    .select([
      'pairs.id',
      'pairs.publication_id',
      'pairs.case_key',
      'a.system as a_system',
      'b.system as b_system',
      'pairs.batched',
      'cases.instruction',
      'cases.checklist',
      'cases.category',
      'a.content as a',
      'b.content as b',
    ])
    .$narrowType<{ a: string; b: string }>()
    .where('pairs.actor_id', '=', actor)
    .where('pairs.family', '=', family)
    .where((eb) =>
      eb.or([
        eb('pairs.batched', '=', 0),
        eb(
          'pairs.publication_id',
          '=',
          eb.selectFrom('active').select('publication_id').whereRef('family', '=', 'pairs.family'),
        ),
      ]),
    )
    .orderBy('pairs.batched')
    .orderBy('pairs.position');
}

function actorVotesQuery(actor: string, family: string) {
  return queries
    .selectFrom('votes')
    .select((eb) => [
      'id',
      'pair_id',
      'case_key',
      'a_system',
      'b_system',
      sql<number>`${eb.ref('exclusion')} IS NULL`.as('counted'),
      'recorded_at',
    ])
    .where('actor_id', '=', actor)
    .where('family', '=', family);
}

function latestUndoQuery(undoCandidates: readonly number[]) {
  return queries
    .selectFrom('votes')
    .select('pair_id')
    .where('id', 'in', (eb) =>
      eb
        .selectFrom(sql<{ value: number }>`json_each(${JSON.stringify(undoCandidates)})`.as('ids'))
        .select('value'),
    )
    .where('exclusion', 'is', null)
    .orderBy('id', 'desc')
    .limit(1);
}

function pairCasesQuery(family: string) {
  return queries
    .selectFrom('publication_pair_cases')
    .select(['low_system', 'high_system', 'case_keys'])
    .where('publication_id', '=', (eb) =>
      eb.selectFrom('active').select('publication_id').where('family', '=', family),
    );
}

/** Issues from the actor's snapshot while its publication, pending ID and vote history still agree. */
function canIssue(
  actor: string,
  family: string,
  publication: string,
  storedPair: string | null,
  voteCut: number,
) {
  const eb = expressionBuilder<Database>();
  const currentPair = eb
    .selectFrom('pairs')
    .select('id')
    .where('actor_id', '=', actor)
    .where('family', '=', family);
  return eb.and([
    eb.exists(
      eb
        .selectFrom('active')
        .select(sql<1>`1`.as('ok'))
        .where('family', '=', family)
        .where('publication_id', '=', publication),
    ),
    sql<boolean>`(${currentPair}) IS ${storedPair}`,
    eb.not(
      eb.exists(
        eb
          .selectFrom('batch_pairs')
          .select(sql<1>`1`.as('ok'))
          .where('actor_id', '=', actor)
          .where('family', '=', family)
          .where('publication_id', '=', publication),
      ),
    ),
    sql<boolean>`NOT EXISTS (SELECT 1 FROM votes INDEXED BY votes_family WHERE family = ${family} AND id > ${voteCut} AND actor_id = ${actor})`,
  ]);
}

function issueQuery(
  actor: string,
  family: string,
  publication: string,
  now: number,
  storedPair: string | null,
  voteCut: number,
  id: string,
  drawn: Drawn,
) {
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
    .expression(
      queries
        .selectNoFrom((eb) => [
          eb.val(id).as('id'),
          eb.val(actor).as('actor_id'),
          eb.val(family).as('family'),
          eb.val(publication).as('publication_id'),
          eb.val(drawn.caseKey).as('case_key'),
          eb.val(drawn.a).as('a_output'),
          eb.val(drawn.b).as('b_output'),
          eb.val(now).as('issued_at'),
        ])
        .where(() => canIssue(actor, family, publication, storedPair, voteCut)),
    )
    .onConflict((oc) =>
      oc
        .columns(['actor_id', 'family'])
        .doUpdateSet((eb) => ({
          id: eb.ref('excluded.id'),
          publication_id: eb.ref('excluded.publication_id'),
          case_key: eb.ref('excluded.case_key'),
          a_output: eb.ref('excluded.a_output'),
          b_output: eb.ref('excluded.b_output'),
          issued_at: eb.ref('excluded.issued_at'),
        }))
        .whereRef('pairs.publication_id', '<>', 'excluded.publication_id'),
    );
}

function issueBatchQuery(
  actor: string,
  family: string,
  publication: string,
  now: number,
  storedPair: string | null,
  voteCut: number,
  rows: readonly ({ id: string } & Drawn)[],
) {
  return queries
    .insertInto('batch_pairs')
    .columns([
      'id',
      'actor_id',
      'family',
      'publication_id',
      'case_key',
      'a_output',
      'b_output',
      'issued_at',
      'position',
    ])
    .expression((eb) =>
      eb
        .selectFrom(
          sql<{ key: number; value: string }>`json_each(${JSON.stringify(rows)})`.as('slot'),
        )
        .select((sub) => [
          sql<string>`json_extract(${sub.ref('slot.value')}, '$.id')`.as('id'),
          sub.val(actor).as('actor_id'),
          sub.val(family).as('family'),
          sub.val(publication).as('publication_id'),
          sql<string>`json_extract(${sub.ref('slot.value')}, '$.caseKey')`.as('case_key'),
          sql<string>`json_extract(${sub.ref('slot.value')}, '$.a')`.as('a_output'),
          sql<string>`json_extract(${sub.ref('slot.value')}, '$.b')`.as('b_output'),
          sub.val(now).as('issued_at'),
          'slot.key as position',
        ])
        .where(() => canIssue(actor, family, publication, storedPair, voteCut)),
    );
}

function replaceQuery(
  table: 'pairs' | 'batch_pairs',
  nextId: string,
  drawn: Drawn,
  now: number,
  pair: string,
  actor: string,
  [lowSystem, highSystem]: SystemPair,
) {
  const base =
    table === 'batch_pairs'
      ? queries.updateTable('batch_pairs as chosen')
      : queries.updateTable('pairs as chosen');
  return base
    .set({
      id: nextId,
      case_key: drawn.caseKey,
      a_output: drawn.a,
      b_output: drawn.b,
      issued_at: now,
    })
    .where('chosen.id', '=', pair)
    .where('chosen.actor_id', '=', actor)
    .where('chosen.publication_id', '=', (eb) =>
      eb.selectFrom('active').select('publication_id').whereRef('family', '=', 'chosen.family'),
    )
    .where((eb) =>
      eb.not(
        eb.exists(
          eb
            .selectFrom('pending_pairs as other')
            .innerJoin('outputs as a', (join) =>
              join
                .onRef('a.publication_id', '=', 'other.publication_id')
                .onRef('a.output_key', '=', 'other.a_output'),
            )
            .innerJoin('outputs as b', (join) =>
              join
                .onRef('b.publication_id', '=', 'other.publication_id')
                .onRef('b.output_key', '=', 'other.b_output'),
            )
            .select(sql<1>`1`.as('ok'))
            .where('other.actor_id', '=', actor)
            .whereRef('other.family', '=', 'chosen.family')
            .whereRef('other.publication_id', '=', 'chosen.publication_id')
            .where('other.case_key', '=', drawn.caseKey)
            .where('a.system', 'in', [lowSystem, highSystem])
            .where('b.system', 'in', [lowSystem, highSystem]),
        ),
      ),
    )
    .where((eb) =>
      eb.not(
        eb.exists(
          eb
            .selectFrom('votes')
            .select(sql<1>`1`.as('ok'))
            .where('actor_id', '=', actor)
            .whereRef('family', '=', 'chosen.family')
            .where('case_key', '=', drawn.caseKey)
            .where((sub) =>
              sub(sql<string>`min(${sub.ref('a_system')}, ${sub.ref('b_system')})`, '=', lowSystem),
            )
            .where((sub) =>
              sub(
                sql<string>`max(${sub.ref('a_system')}, ${sub.ref('b_system')})`,
                '=',
                highSystem,
              ),
            ),
        ),
      ),
    );
}

type ActiveRow = InferResult<ReturnType<typeof activeQuery>>[number];
type PendingRow = InferResult<ReturnType<typeof pendingQuery>>[number];
type ActorVoteRow = InferResult<ReturnType<typeof actorVotesQuery>>[number];
type PairCasesRow = InferResult<ReturnType<typeof pairCasesQuery>>[number];

const key = (...parts: string[]) => JSON.stringify(parts);
const sorted = (a: string, b: string): SystemPair => (a < b ? [a, b] : [b, a]);

export function ranker(actorKey: string, actor: string, family: string): Rank {
  const sign = signer(actorKey);
  return async (...parts) => hex(await sign(key('draw', actor, family, ...parts)));
}

/** Cases still open to the voter with differing passing samples from both systems. */
export function candidates(
  facts: readonly PairCasesRow[],
  voted: readonly Pick<ActorVoteRow, 'case_key' | 'a_system' | 'b_system'>[],
): Map<string, CandidateGroup> {
  const history = Map.groupBy(voted, ({ a_system: a, b_system: b }) => key(...sorted(a, b)));
  const groups = new Map<string, CandidateGroup>();

  for (const { low_system: low, high_system: high, case_keys } of facts) {
    const systems = sorted(low, high);
    const pairKey = key(...systems);
    const caseKeys = JSON.parse(case_keys) as string[];
    const votes = history.get(pairKey);
    const done = votes && new Set(votes.map(({ case_key }) => case_key));
    const remaining = done ? caseKeys.filter((caseKey) => !done.has(caseKey)) : caseKeys;

    if (remaining.length > 0) {
      groups.set(pairKey, { systems, caseKeys: remaining });
    }
  }

  return groups;
}

async function ordered<T>(items: readonly T[], rank: (item: T) => Promise<string>) {
  const ranks = await Promise.all(items.map(rank));
  return items
    .map((item, index) => ({ item, rank: ranks[index] }))
    .sort((a, b) => (a.rank < b.rank ? -1 : a.rank > b.rank ? 1 : 0));
}

/** One system pair's cases in the voter's order. */
async function caseOrder(rank: Rank, { systems, caseKeys }: CandidateGroup) {
  const order = await ordered(caseKeys, (caseKey) => rank('case', ...systems, caseKey));
  return order.map(({ item }) => item);
}

/** The lowest-ranked differing sample pair, with its rank's lowest bit choosing the sides. */
async function place(
  db: D1Database,
  publication: string,
  rank: Rank,
  { caseKey, systems: [lowSystem, highSystem] }: Comparison,
): Promise<Drawn> {
  const outputs = await all(
    db,
    queries
      .selectFrom('outputs')
      .select(['output_key', 'case_key', 'system', 'content_digest'])
      .where('publication_id', '=', publication)
      .where('case_key', '=', caseKey)
      .where('system', 'in', [lowSystem, highSystem])
      .where('verdict', '=', 'pass'),
  );
  const lows = outputs.filter(({ system }) => system === lowSystem);
  const highs = outputs.filter(({ system }) => system === highSystem);
  const samples = lows.flatMap((a) =>
    highs.filter((b) => a.content_digest !== b.content_digest).map((b) => [a, b] as const),
  );
  const [firstSample] = await ordered(samples, ([a, b]) =>
    rank('sample', a.output_key, b.output_key),
  );
  const [low, high] = firstSample.item;
  const swap = Number.parseInt(firstSample.rank.slice(-1), 16) & 1;
  return {
    caseKey,
    a: (swap ? high : low).output_key,
    b: (swap ? low : high).output_key,
  };
}

/**
 * The first case, in the voter's order, of the system pair with the fewest counted votes in the
 * family, ties going to the voter's order of system pairs. `groups` must not be empty.
 */
export async function draw(
  rank: Rank,
  groups: ReadonlyMap<string, CandidateGroup>,
  coverage: ReadonlyMap<string, number>,
): Promise<Comparison> {
  const pairs = [...groups].map(([pairKey, group]) => ({
    group,
    votes: coverage.get(pairKey) ?? 0,
  }));
  const fewest = Math.min(...pairs.map(({ votes }) => votes));
  const [chosen] = await ordered(
    pairs.filter(({ votes }) => votes === fewest),
    ({ group }) => rank('pair', ...group.systems),
  );
  const { group } = chosen.item;
  const [caseKey] = await caseOrder(rank, group);
  return { caseKey, systems: group.systems };
}

/** The case after `current` in the voter's order for one system pair, wrapping around. */
export async function next(
  rank: Rank,
  group: CandidateGroup | undefined,
  current: string,
): Promise<Comparison | null> {
  if (group === undefined) {
    return null;
  }

  const order = await caseOrder(rank, group);
  const caseKey = order[(order.indexOf(current) + 1) % order.length];
  return caseKey === current ? null : { caseKey, systems: group.systems };
}

/** An actor's standing in a family, as one batch reads it. */
interface Standing {
  active: ActiveRow;
  /** The pending pair, while it belongs to the active publication. */
  pending: PendingRow | undefined;
  offered: PendingRow[];
  storedPair: string | null;
  voted: ActorVoteRow[];
  groups: ReadonlyMap<string, CandidateGroup>;
}

/** Reads the family's active publication, or 404 `no_round`, with the actor's standing in it. */
async function read(db: D1Database, actor: string, family: string): Promise<Standing> {
  const [activeRows, pendingRows, voteRows, pairRows] = await batch(db, [
    activeQuery(family),
    pendingQuery(actor, family),
    actorVotesQuery(actor, family),
    pairCasesQuery(family),
  ]);
  const active = activeRows.results.at(0);
  if (active === undefined) {
    throw new ApiError(404, 'no_round');
  }

  const stored = pendingRows.results;
  const offered = stored.filter(({ publication_id }) => publication_id === active.id);
  const voted = voteRows.results;
  return {
    active,
    pending: offered.at(0),
    offered,
    storedPair: stored.find(({ batched }) => !batched)?.id ?? null,
    voted,
    groups: candidates(pairRows.results, voted),
  };
}

export interface PairSnapshot {
  state: PairState;
  undoPair: string | null;
}

function latestUndoPair(voted: ActorVoteRow[], now: number): string | null {
  let latest: ActorVoteRow | undefined;
  for (const vote of voted) {
    if (
      vote.counted &&
      vote.recorded_at > now - UNDO_MS &&
      (latest === undefined || vote.id > latest.id)
    ) {
      latest = vote;
    }
  }
  return latest?.pair_id ?? null;
}

function view(
  { active, voted, groups }: Standing,
  pending: PendingRow | undefined,
  now: number,
  undoPair = latestUndoPair(voted, now),
): PairSnapshot {
  const state: PairState = {
    pair: pending === undefined ? null : pairView(active, pending),
    votes: voted.length,
    remaining: [...groups.values()].reduce((sum, group) => sum + group.caseKeys.length, 0),
    undo: undoPair !== null,
  };
  return { state, undoPair };
}

function pairView(active: ActiveRow, pending: PendingRow): PairView {
  return {
    id: pending.id,
    instruction: pending.instruction,
    checklist: JSON.parse(pending.checklist) as string[],
    category: pending.category,
    ...(active.render === 'html'
      ? { render: active.render }
      : { render: active.render, a: pending.a, b: pending.b }),
    defects: JSON.parse(active.defects) as Defect[],
  };
}

function reserve(groups: Map<string, CandidateGroup>, { caseKey, systems }: Comparison) {
  const pairKey = key(...systems);
  const group = groups.get(pairKey);
  if (group === undefined) {
    return;
  }
  const caseKeys = group.caseKeys.filter((remaining) => remaining !== caseKey);
  if (caseKeys.length === 0) {
    groups.delete(pairKey);
  } else {
    groups.set(pairKey, { ...group, caseKeys });
  }
}

function available({ groups, offered }: Standing, except?: string) {
  const remaining = new Map(groups);
  for (const pair of offered) {
    if (pair.id !== except) {
      reserve(remaining, { caseKey: pair.case_key, systems: sorted(pair.a_system, pair.b_system) });
    }
  }
  return remaining;
}

async function currentCoverage(db: D1Database, family: string) {
  const coverage = await all(
    db,
    queries
      .selectFrom('pair_vote_counts')
      .select(['low_system as low', 'high_system as high', 'votes'])
      .where('family', '=', family),
  );
  return new Map(coverage.map(({ low, high, votes }) => [key(...sorted(low, high)), votes]));
}

/** The actor's pending pair in a family, drawn afresh when it belongs to no active publication. */
export async function pairState(
  db: D1Database,
  actorKey: string,
  actor: string,
  family: string,
  now: number,
): Promise<PairSnapshot> {
  const initial = await read(db, actor, family);
  const publication = initial.active.id;
  let standing = initial;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (standing.pending !== undefined || standing.groups.size === 0) {
      return view(standing, standing.pending, now);
    }
    const issued = await issue(db, actorKey, actor, family, standing, now);
    if (issued !== undefined) {
      return issued;
    }

    if (attempt === 0) {
      standing = await read(db, actor, family);
      if (standing.active.id !== publication) {
        break;
      }
    }
  }
  throw new ApiError(409, 'stale_pair');
}

async function issue(
  db: D1Database,
  actorKey: string,
  actor: string,
  family: string,
  standing: Standing,
  now: number,
): Promise<PairSnapshot | undefined> {
  const { active, groups, storedPair, voted } = standing;
  const publication = active.id;
  const rank = ranker(actorKey, actor, family);
  const comparison = await draw(rank, groups, await currentCoverage(db, family));
  const drawn = await place(db, publication, rank, comparison);
  const id = crypto.randomUUID();

  const undoCandidates = voted
    .filter((vote) => vote.recorded_at > now - UNDO_MS)
    .map(({ id }) => id);
  const [, pending, undo] = await batch(db, [
    issueQuery(actor, family, publication, now, storedPair, active.vote_cut, id, drawn),
    pendingQuery(actor, family),
    latestUndoQuery(undoCandidates),
  ]);
  const current = pending.results.find(({ publication_id }) => publication_id === publication);
  const undoPair = undo.results.at(0)?.pair_id ?? null;
  return current?.id === id ? view(standing, current, now, undoPair) : undefined;
}

/** Offers at most one open batch per browser and family. Active receipts remain stable on retry. */
export async function pairBatch(
  db: D1Database,
  actorKey: string,
  actor: string,
  family: string,
  count: number,
  now: number,
): Promise<PairView[]> {
  const standing = await read(db, actor, family);
  const { active, offered, storedPair } = standing;
  const existing = () => offered.slice(0, count).map((pair) => pairView(active, pair));
  if (offered.some(({ batched }) => batched) || offered.length >= count) {
    return existing();
  }
  const remaining = available(standing);
  if (remaining.size === 0) {
    return existing();
  }

  const rank = ranker(actorKey, actor, family);
  const counts = await currentCoverage(db, family);
  const comparisons: Comparison[] = [];
  while (remaining.size > 0 && offered.length + comparisons.length < count) {
    const comparison = await draw(rank, remaining, counts);
    reserve(remaining, comparison);
    const pairKey = key(...comparison.systems);
    counts.set(pairKey, (counts.get(pairKey) ?? 0) + 1);
    comparisons.push(comparison);
  }
  const rows = await Promise.all(
    comparisons.map(async (comparison) => ({
      id: crypto.randomUUID(),
      ...(await place(db, active.id, rank, comparison)),
    })),
  );
  const [inserted, pending] = await batch(db, [
    issueBatchQuery(actor, family, active.id, now, storedPair, active.vote_cut, rows),
    pendingQuery(actor, family),
  ]);
  const current = pending.results.filter(({ publication_id }) => publication_id === active.id);
  if (inserted.changes === 0 && !current.some(({ batched }) => batched)) {
    throw new ApiError(409, 'stale_pair');
  }
  return current.slice(0, count).map((pair) => pairView(active, pair));
}

/**
 * Replaces the pending pair with the next case for the same two systems under a new ID, keeping
 * it when that system pair has no other case left.
 */
export async function skip(
  db: D1Database,
  actorKey: string,
  actor: string,
  pair: string,
  now: number,
): Promise<PairSnapshot> {
  const owner = await first(
    db,
    queries
      .selectFrom('pending_pairs as pairs')
      .innerJoin('active', (join) =>
        join
          .onRef('active.family', '=', 'pairs.family')
          .onRef('active.publication_id', '=', 'pairs.publication_id'),
      )
      .select('pairs.family')
      .where('pairs.id', '=', pair)
      .where('pairs.actor_id', '=', actor),
  );
  if (owner === null) {
    throw new ApiError(409, 'stale_pair');
  }

  const { family } = owner;
  const standing = await read(db, actor, family);
  const { pending } = standing;
  if (pending?.id !== pair) {
    throw new ApiError(409, 'stale_pair');
  }

  const systems = sorted(pending.a_system, pending.b_system);
  const rank = ranker(actorKey, actor, family);
  const comparison = await next(
    rank,
    available(standing, pair).get(key(...systems)),
    pending.case_key,
  );
  if (comparison === null) {
    return view(standing, pending, now);
  }

  const drawn = await place(db, standing.active.id, rank, comparison);
  const [replaced, current] = await batch(db, [
    replaceQuery(
      pending.batched ? 'batch_pairs' : 'pairs',
      crypto.randomUUID(),
      drawn,
      now,
      pair,
      actor,
      systems,
    ),
    pendingQuery(actor, family),
  ]);
  if (replaced.changes === 0) {
    throw new ApiError(409, 'stale_pair');
  }
  const nextPair = current.results.find(
    ({ publication_id }) => publication_id === standing.active.id,
  );
  return view(standing, nextPair, now);
}
