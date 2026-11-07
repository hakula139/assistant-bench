import type { PairState } from '@/lib/arena';
import type { Category } from '@/lib/categories';
import type { Defect } from '@/lib/defects';

import { ApiError } from './api';
import { hex, signer } from './hmac';
import { UNDO_MS } from './limits';

interface ActiveRow {
  id: string;
  render: 'text' | 'diff';
  defects: string;
}

interface PendingRow {
  id: string;
  publication_id: string;
  case_key: string;
  a_system: string;
  b_system: string;
  instruction: string;
  checklist: string;
  category: Category;
  a: string;
  b: string;
}

interface VoteRow {
  case_key: string;
  a_system: string;
  b_system: string;
  counted: number;
  recorded_at: number;
}

export interface PassingOutput {
  output_key: string;
  case_key: string;
  system: string;
  content_digest: string;
}

export interface PairCases {
  low_system: string;
  high_system: string;
  case_keys: string;
}

export interface Comparison {
  caseKey: string;
  systems: readonly [string, string];
}

/** A case and the outputs shown as A and B. */
interface Drawn {
  caseKey: string;
  a: string;
  b: string;
}

/** Ranks items in one voter's fixed private order, as hex digests that compare as strings. */
export type Rank = (...parts: string[]) => Promise<string>;

const ACTIVE = `
  SELECT publications.id, publications.render, publications.defects
  FROM active JOIN publications ON publications.id = active.publication_id
  WHERE active.family = ?`;
const PENDING = `
  SELECT pairs.id, pairs.publication_id, pairs.case_key, a.system AS a_system, b.system AS b_system,
    cases.instruction, cases.checklist, cases.category, a.content AS a, b.content AS b
  FROM pairs
  JOIN cases ON cases.publication_id = pairs.publication_id AND cases.case_key = pairs.case_key
  JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
  JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
  WHERE pairs.actor_id = ? AND pairs.family = ?`;
const ACTOR_VOTES = `
  SELECT case_key, a_system, b_system, exclusion IS NULL AS counted, recorded_at
  FROM votes WHERE actor_id = ? AND family = ?`;
const PAIR_CASES = `
  SELECT low_system, high_system, case_keys FROM publication_pair_cases
  WHERE publication_id = (SELECT publication_id FROM active WHERE family = ?)`;
const SAMPLES = `
  SELECT output_key, case_key, system, content_digest FROM outputs
  WHERE publication_id = ? AND case_key = ? AND system IN (?, ?) AND verdict = 'pass'`;
const COVERAGE = `
  SELECT low_system AS low, high_system AS high, votes
  FROM pair_vote_counts WHERE family = ?`;
/** Keeps a pair that another request issued on the same publication meanwhile. */
const ISSUE = `
  INSERT INTO pairs (id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at)
  SELECT ?, ?, ?, ?, ?, ?, ?, ?
  WHERE EXISTS (SELECT 1 FROM active WHERE family = ? AND publication_id = ?)
  ON CONFLICT (actor_id, family) DO UPDATE SET
    id = excluded.id, publication_id = excluded.publication_id, case_key = excluded.case_key,
    a_output = excluded.a_output, b_output = excluded.b_output, issued_at = excluded.issued_at
  WHERE pairs.publication_id <> excluded.publication_id`;
const PAIR_FAMILY = `
  SELECT pairs.family FROM pairs
  JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  WHERE pairs.id = ? AND pairs.actor_id = ?`;
const REPLACE = `
  UPDATE pairs SET id = ?, case_key = ?, a_output = ?, b_output = ?, issued_at = ?
  WHERE id = ? AND actor_id = ?
    AND publication_id = (SELECT publication_id FROM active WHERE family = pairs.family)`;

const key = (...parts: string[]) => JSON.stringify(parts);
const sorted = (a: string, b: string) => (a < b ? ([a, b] as const) : ([b, a] as const));

export function ranker(actorKey: string, actor: string, family: string): Rank {
  const sign = signer(actorKey);
  return async (...parts) => hex(await sign(key('draw', actor, family, ...parts)));
}

/** Every comparison whose two systems pass on the case with some differing pair of samples. */
export function candidates(
  facts: readonly PairCases[],
  voted: readonly Pick<VoteRow, 'case_key' | 'a_system' | 'b_system'>[],
): Comparison[] {
  const done = new Set(
    voted.map((vote) => key(vote.case_key, ...sorted(vote.a_system, vote.b_system))),
  );

  return facts.flatMap(({ low_system: low, high_system: high, case_keys }) => {
    const systems = sorted(low, high);
    return (JSON.parse(case_keys) as string[]).flatMap((caseKey) =>
      done.has(key(caseKey, ...systems)) ? [] : [{ caseKey, systems }],
    );
  });
}

async function ordered<T>(items: readonly T[], rank: (item: T) => Promise<string>) {
  const ranks = await Promise.all(items.map(rank));
  return items
    .map((item, index) => ({ item, rank: ranks[index] }))
    .sort((a, b) => (a.rank < b.rank ? -1 : a.rank > b.rank ? 1 : 0));
}

/** One system pair's comparisons in the voter's order of their cases. */
async function caseOrder(rank: Rank, comparisons: readonly Comparison[]) {
  const order = await ordered(comparisons, ({ caseKey, systems }) =>
    rank('case', ...systems, caseKey),
  );
  return order.map(({ item }) => item);
}

/** The lowest-ranked differing sample pair, with its rank's lowest bit choosing the sides. */
async function place(
  db: D1Database,
  publication: string,
  rank: Rank,
  { caseKey, systems: [lowSystem, highSystem] }: Comparison,
): Promise<Drawn> {
  const outputs = await db
    .prepare(SAMPLES)
    .bind(publication, caseKey, lowSystem, highSystem)
    .all<PassingOutput>();
  const lows = outputs.results.filter(({ system }) => system === lowSystem);
  const highs = outputs.results.filter(({ system }) => system === highSystem);
  const samples = lows.flatMap((a) =>
    highs.filter((b) => a.content_digest !== b.content_digest).map((b) => [a, b] as const),
  );
  const [first] = await ordered(samples, ([a, b]) => rank('sample', a.output_key, b.output_key));
  const [low, high] = first.item;
  const swap = Number.parseInt(first.rank.slice(-1), 16) & 1;
  return {
    caseKey,
    a: (swap ? high : low).output_key,
    b: (swap ? low : high).output_key,
  };
}

/**
 * The first case, in the voter's order, of the system pair with the fewest counted votes in the
 * family, ties going to the voter's order of system pairs. `comparisons` must not be empty.
 */
export async function draw(
  rank: Rank,
  comparisons: readonly Comparison[],
  coverage: ReadonlyMap<string, number>,
): Promise<Comparison> {
  const pairs = [...new Map(comparisons.map(({ systems }) => [key(...systems), systems])).values()];
  const votes = (systems: readonly string[]) => coverage.get(key(...systems)) ?? 0;
  const fewest = Math.min(...pairs.map(votes));
  const [chosen] = await ordered(
    pairs.filter((systems) => votes(systems) === fewest),
    (systems) => rank('pair', ...systems),
  );
  const [first] = await caseOrder(
    rank,
    comparisons.filter(({ systems }) => key(...systems) === key(...chosen.item)),
  );
  return first;
}

/** The case after `current` in the voter's order for one system pair, wrapping around. */
export async function next(
  rank: Rank,
  comparisons: readonly Comparison[],
  current: string,
): Promise<Comparison | null> {
  const order = await caseOrder(rank, comparisons);
  const following = order.at(
    (order.findIndex(({ caseKey }) => caseKey === current) + 1) % order.length,
  );
  return following === undefined || following.caseKey === current ? null : following;
}

/** An actor's standing in a family, as one batch reads it. */
interface Standing {
  active: ActiveRow;
  /** The pending pair, while it belongs to the active publication. */
  pending: PendingRow | undefined;
  voted: VoteRow[];
  comparisons: Comparison[];
}

/** Reads the family's active publication, or 404 `no_round`, with the actor's standing in it. */
async function read(db: D1Database, actor: string, family: string): Promise<Standing> {
  const [activeRows, pendingRows, voteRows, pairRows] = await db.batch([
    db.prepare(ACTIVE).bind(family),
    db.prepare(PENDING).bind(actor, family),
    db.prepare(ACTOR_VOTES).bind(actor, family),
    db.prepare(PAIR_CASES).bind(family),
  ]);
  const active = (activeRows.results as ActiveRow[]).at(0);
  if (active === undefined) {
    throw new ApiError(404, 'no_round');
  }

  const pending = (pendingRows.results as PendingRow[]).at(0);
  const voted = voteRows.results as VoteRow[];
  return {
    active,
    pending: pending?.publication_id === active.id ? pending : undefined,
    voted,
    comparisons: candidates(pairRows.results as PairCases[], voted),
  };
}

function view(
  { active, voted, comparisons }: Standing,
  pending: PendingRow | undefined,
  now: number,
): PairState {
  return {
    pair:
      pending === undefined
        ? null
        : {
            id: pending.id,
            instruction: pending.instruction,
            checklist: JSON.parse(pending.checklist) as string[],
            category: pending.category,
            render: active.render,
            a: pending.a,
            b: pending.b,
            defects: JSON.parse(active.defects) as Defect[],
          },
    votes: voted.length,
    remaining: comparisons.length,
    undo: voted.some((vote) => vote.counted && vote.recorded_at > now - UNDO_MS),
  };
}

/** The actor's pending pair in a family, drawn afresh when it belongs to no active publication. */
export async function pairState(
  db: D1Database,
  actorKey: string,
  actor: string,
  family: string,
  now: number,
): Promise<PairState> {
  const standing = await read(db, actor, family);
  const pending =
    standing.pending ??
    (await issue(db, actorKey, actor, family, standing.active.id, standing.comparisons, now));
  return view(standing, pending, now);
}

async function issue(
  db: D1Database,
  actorKey: string,
  actor: string,
  family: string,
  publication: string,
  comparisons: readonly Comparison[],
  now: number,
): Promise<PendingRow | undefined> {
  if (comparisons.length === 0) {
    return undefined;
  }

  const coverage = await db
    .prepare(COVERAGE)
    .bind(family)
    .all<{ low: string; high: string; votes: number }>();
  const rank = ranker(actorKey, actor, family);
  const comparison = await draw(
    rank,
    comparisons,
    new Map(coverage.results.map(({ low, high, votes }) => [key(low, high), votes])),
  );
  const drawn = await place(db, publication, rank, comparison);
  const [, pending] = await db.batch([
    db
      .prepare(ISSUE)
      .bind(
        crypto.randomUUID(),
        actor,
        family,
        publication,
        drawn.caseKey,
        drawn.a,
        drawn.b,
        now,
        family,
        publication,
      ),
    db.prepare(PENDING).bind(actor, family),
  ]);
  const current = (pending.results as PendingRow[]).at(0);
  if (current?.publication_id !== publication) {
    throw new ApiError(409, 'stale_pair');
  }
  return current;
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
): Promise<PairState> {
  const owner = await db.prepare(PAIR_FAMILY).bind(pair, actor).first<{ family: string }>();
  if (owner === null) {
    throw new ApiError(409, 'stale_pair');
  }

  const { family } = owner;
  const standing = await read(db, actor, family);
  const { pending } = standing;
  if (pending?.id !== pair) {
    throw new ApiError(409, 'stale_pair');
  }

  const systems = key(...sorted(pending.a_system, pending.b_system));
  const rank = ranker(actorKey, actor, family);
  const comparison = await next(
    rank,
    standing.comparisons.filter((comparison) => key(...comparison.systems) === systems),
    pending.case_key,
  );
  if (comparison === null) {
    return view(standing, pending, now);
  }

  const drawn = await place(db, standing.active.id, rank, comparison);
  const [replaced, current] = await db.batch([
    db
      .prepare(REPLACE)
      .bind(crypto.randomUUID(), drawn.caseKey, drawn.a, drawn.b, now, pair, actor),
    db.prepare(PENDING).bind(actor, family),
  ]);
  if (replaced.meta.changes === 0) {
    throw new ApiError(409, 'stale_pair');
  }
  return view(standing, (current.results as PendingRow[]).at(0), now);
}
