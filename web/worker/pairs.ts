import type { PairState } from '@/lib/arena';
import type { Category } from '@/lib/categories';
import type { Defect } from '@/lib/defects';

import { hex, signer } from './actors';
import { ApiError } from './api';
import { UNDO_MS } from './limits';

interface ActiveRow {
  id: string;
  render: 'text' | 'diff';
  defects: string;
}

interface PendingRow {
  id: string;
  publication_id: string;
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

/** A case and two systems the actor can still compare, with every sample pair that differs. */
export interface Comparison {
  caseKey: string;
  systems: readonly [string, string];
  samples: readonly (readonly [PassingOutput, PassingOutput])[];
}

/** A case and the outputs shown as A and B. */
export interface Drawn {
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
  SELECT pairs.id, pairs.publication_id, cases.instruction, cases.checklist, cases.category,
    a.content AS a, b.content AS b
  FROM pairs
  JOIN cases ON cases.publication_id = pairs.publication_id AND cases.case_key = pairs.case_key
  JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
  JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
  WHERE pairs.actor_id = ? AND pairs.family = ?`;
const ACTOR_VOTES = `
  SELECT case_key, a_system, b_system, exclusion IS NULL AS counted, recorded_at
  FROM votes WHERE actor_id = ? AND family = ?`;
const PASSING = `
  SELECT output_key, case_key, system, content_digest FROM outputs
  WHERE publication_id = (SELECT publication_id FROM active WHERE family = ?) AND verdict = 'pass'`;
const COVERAGE = `
  SELECT min(a_system, b_system) AS low, max(a_system, b_system) AS high, count(*) AS votes
  FROM votes WHERE family = ? AND exclusion IS NULL GROUP BY low, high`;
// Keeps a pair that another request issued on the same publication meanwhile.
const ISSUE = `
  INSERT INTO pairs (id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT (actor_id, family) DO UPDATE SET
    id = excluded.id, publication_id = excluded.publication_id, case_key = excluded.case_key,
    a_output = excluded.a_output, b_output = excluded.b_output, issued_at = excluded.issued_at
  WHERE pairs.publication_id <> excluded.publication_id`;
const PENDING_BY_ID = `
  SELECT pairs.family, pairs.case_key, a.system AS a_system, b.system AS b_system
  FROM pairs
  JOIN active ON active.family = pairs.family AND active.publication_id = pairs.publication_id
  JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
  JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
  WHERE pairs.id = ? AND pairs.actor_id = ?`;
const REPLACE = `
  UPDATE pairs SET id = ?, case_key = ?, a_output = ?, b_output = ?, issued_at = ?
  WHERE id = ? AND actor_id = ?`;

const key = (...parts: string[]) => JSON.stringify(parts);
const sorted = (a: string, b: string) => (a < b ? ([a, b] as const) : ([b, a] as const));

export function ranker(actorKey: string, actor: string, family: string): Rank {
  const sign = signer(actorKey);
  return async (...parts) => hex(await sign(key('draw', actor, family, ...parts)));
}

/** Every comparison whose two systems pass on the case with some differing pair of samples. */
export function candidates(
  outputs: readonly PassingOutput[],
  voted: readonly Pick<VoteRow, 'case_key' | 'a_system' | 'b_system'>[],
): Comparison[] {
  const done = new Set(
    voted.map((vote) => key(vote.case_key, ...sorted(vote.a_system, vote.b_system))),
  );

  return [...Map.groupBy(outputs, (output) => output.case_key)].flatMap(([caseKey, outputs]) => {
    const systems = Map.groupBy(outputs, (output) => output.system);
    const names = [...systems.keys()].sort();

    return names.flatMap((low, index) =>
      names.slice(index + 1).flatMap((high) => {
        const samples = (systems.get(low) ?? []).flatMap((a) =>
          (systems.get(high) ?? [])
            .filter((b) => a.content_digest !== b.content_digest)
            .map((b) => [a, b] as const),
        );
        return samples.length === 0 || done.has(key(caseKey, low, high))
          ? []
          : [{ caseKey, systems: [low, high] as const, samples }];
      }),
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
async function place(rank: Rank, comparison: Comparison): Promise<Drawn> {
  const [first] = await ordered(comparison.samples, ([a, b]) =>
    rank('sample', a.output_key, b.output_key),
  );
  const [low, high] = first.item;
  const swap = Number.parseInt(first.rank.slice(-1), 16) & 1;
  return {
    caseKey: comparison.caseKey,
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
): Promise<Drawn> {
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
  return place(rank, first);
}

/** The case after `current` in the voter's order for one system pair, wrapping around. */
export async function next(
  rank: Rank,
  comparisons: readonly Comparison[],
  current: string,
): Promise<Drawn | null> {
  const order = await caseOrder(rank, comparisons);
  const following = order.at(
    (order.findIndex(({ caseKey }) => caseKey === current) + 1) % order.length,
  );
  return following === undefined || following.caseKey === current ? null : place(rank, following);
}

/** The actor's pending pair in a family, drawn afresh when it belongs to no active publication. */
export async function pairState(
  db: D1Database,
  actorKey: string,
  actor: string,
  family: string,
  now: number,
): Promise<PairState> {
  const [activeRows, pendingRows, voteRows, passingRows] = await db.batch([
    db.prepare(ACTIVE).bind(family),
    db.prepare(PENDING).bind(actor, family),
    db.prepare(ACTOR_VOTES).bind(actor, family),
    db.prepare(PASSING).bind(family),
  ]);
  const active = (activeRows.results as ActiveRow[]).at(0);
  if (active === undefined) {
    throw new ApiError(404, 'no_round');
  }

  const voted = voteRows.results as VoteRow[];
  const comparisons = candidates(passingRows.results as PassingOutput[], voted);
  let pending = (pendingRows.results as PendingRow[]).at(0);
  if (pending?.publication_id !== active.id) {
    pending = await issue(db, actorKey, actor, family, active.id, comparisons, now);
  }

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
  const drawn = await draw(
    ranker(actorKey, actor, family),
    comparisons,
    new Map(coverage.results.map(({ low, high, votes }) => [key(low, high), votes])),
  );
  const [, pending] = await db.batch([
    db
      .prepare(ISSUE)
      .bind(crypto.randomUUID(), actor, family, publication, drawn.caseKey, drawn.a, drawn.b, now),
    db.prepare(PENDING).bind(actor, family),
  ]);
  return (pending.results as PendingRow[]).at(0);
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
  const pending = await db
    .prepare(PENDING_BY_ID)
    .bind(pair, actor)
    .first<{ family: string; case_key: string; a_system: string; b_system: string }>();
  if (pending === null) {
    throw new ApiError(409, 'stale_pair');
  }

  const { family } = pending;
  const [voteRows, passingRows] = await db.batch([
    db.prepare(ACTOR_VOTES).bind(actor, family),
    db.prepare(PASSING).bind(family),
  ]);
  const systems = key(...sorted(pending.a_system, pending.b_system));
  const drawn = await next(
    ranker(actorKey, actor, family),
    candidates(passingRows.results as PassingOutput[], voteRows.results as VoteRow[]).filter(
      (comparison) => key(...comparison.systems) === systems,
    ),
    pending.case_key,
  );
  if (drawn !== null) {
    const { meta } = await db
      .prepare(REPLACE)
      .bind(crypto.randomUUID(), drawn.caseKey, drawn.a, drawn.b, now, pair, actor)
      .run();
    if (meta.changes === 0) {
      throw new ApiError(409, 'stale_pair');
    }
  }
  return pairState(db, actorKey, actor, family, now);
}
