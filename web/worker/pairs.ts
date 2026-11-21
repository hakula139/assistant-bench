import type { PairState } from '@/lib/arena';
import type { Category } from '@/lib/categories';
import type { Defect } from '@/lib/defects';

import { ApiError } from './api';
import { hex, signer } from './hmac';
import { UNDO_MS } from './limits';

interface ActiveRow {
  id: string;
  vote_cut: number;
  render: 'text' | 'diff' | 'html';
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
  id: number;
  pair_id: string;
  case_key: string;
  a_system: string;
  b_system: string;
  counted: number;
  recorded_at: number;
}

interface PassingOutput {
  output_key: string;
  case_key: string;
  system: string;
  content_digest: string;
}

interface PairCases {
  low_system: string;
  high_system: string;
  case_keys: string;
}

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

const ACTIVE = `
  SELECT publications.id, publications.render, publications.defects,
    (SELECT coalesce(max(id), 0) FROM votes) AS vote_cut
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
  SELECT id, pair_id, case_key, a_system, b_system, exclusion IS NULL AS counted, recorded_at
  FROM votes WHERE actor_id = ? AND family = ?`;
const LATEST_UNDO = `
  SELECT pair_id FROM votes WHERE id IN (SELECT value FROM json_each(?)) AND exclusion IS NULL
  ORDER BY id DESC LIMIT 1`;
const PAIR_CASES = `
  SELECT low_system, high_system, case_keys FROM publication_pair_cases
  WHERE publication_id = (SELECT publication_id FROM active WHERE family = ?)`;
const SAMPLES = `
  SELECT output_key, case_key, system, content_digest FROM outputs
  WHERE publication_id = ? AND case_key = ? AND system IN (?, ?) AND verdict = 'pass'`;
const COVERAGE = `
  SELECT low_system AS low, high_system AS high, votes
  FROM pair_vote_counts WHERE family = ?`;
/** Issues from the actor's snapshot while its publication, pending ID and vote history still agree. */
const ISSUE = `
  INSERT INTO pairs (id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at)
  SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8
  WHERE EXISTS (SELECT 1 FROM active WHERE family = ?3 AND publication_id = ?4)
    AND (SELECT id FROM pairs WHERE actor_id = ?2 AND family = ?3) IS ?9
    AND NOT EXISTS (
      SELECT 1 FROM votes INDEXED BY votes_family WHERE family = ?3 AND id > ?10 AND actor_id = ?2
    )
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
const sorted = (a: string, b: string): SystemPair => (a < b ? [a, b] : [b, a]);

export function ranker(actorKey: string, actor: string, family: string): Rank {
  const sign = signer(actorKey);
  return async (...parts) => hex(await sign(key('draw', actor, family, ...parts)));
}

/** Cases still open to the voter with differing passing samples from both systems. */
export function candidates(
  facts: readonly PairCases[],
  voted: readonly Pick<VoteRow, 'case_key' | 'a_system' | 'b_system'>[],
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
  storedPair: string | null;
  voted: VoteRow[];
  groups: ReadonlyMap<string, CandidateGroup>;
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
    storedPair: pending?.id ?? null,
    voted,
    groups: candidates(pairRows.results as PairCases[], voted),
  };
}

export interface PairSnapshot {
  state: PairState;
  undoPair: string | null;
}

function latestUndoPair(voted: VoteRow[], now: number): string | null {
  let latest: VoteRow | undefined;
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
    pair:
      pending === undefined
        ? null
        : {
            id: pending.id,
            instruction: pending.instruction,
            checklist: JSON.parse(pending.checklist) as string[],
            category: pending.category,
            ...(active.render === 'html'
              ? { render: active.render }
              : { render: active.render, a: pending.a, b: pending.b }),
            defects: JSON.parse(active.defects) as Defect[],
          },
    votes: voted.length,
    remaining: [...groups.values()].reduce((sum, group) => sum + group.caseKeys.length, 0),
    undo: undoPair !== null,
  };
  return { state, undoPair };
}

/** The actor's pending pair in a family, drawn afresh when it belongs to no active publication. */
export async function pairState(
  db: D1Database,
  actorKey: string,
  actor: string,
  family: string,
  now: number,
): Promise<PairSnapshot> {
  const first = await read(db, actor, family);
  const publication = first.active.id;
  let standing = first;
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
  const coverage = await db
    .prepare(COVERAGE)
    .bind(family)
    .all<{ low: string; high: string; votes: number }>();
  const rank = ranker(actorKey, actor, family);
  const comparison = await draw(
    rank,
    groups,
    new Map(coverage.results.map(({ low, high, votes }) => [key(...sorted(low, high)), votes])),
  );
  const drawn = await place(db, publication, rank, comparison);
  const id = crypto.randomUUID();

  const undoCandidates = voted
    .filter((vote) => vote.recorded_at > now - UNDO_MS)
    .map(({ id }) => id);
  const [, pending, undo] = await db.batch([
    db
      .prepare(ISSUE)
      .bind(
        id,
        actor,
        family,
        publication,
        drawn.caseKey,
        drawn.a,
        drawn.b,
        now,
        storedPair,
        active.vote_cut,
      ),
    db.prepare(PENDING).bind(actor, family),
    db.prepare(LATEST_UNDO).bind(JSON.stringify(undoCandidates)),
  ]);
  const current = (pending.results as PendingRow[]).at(0);
  if (current !== undefined && current.publication_id !== publication) {
    throw new ApiError(409, 'stale_pair');
  }
  const undoPair = (undo.results as Pick<VoteRow, 'pair_id'>[]).at(0)?.pair_id ?? null;
  return current?.id === id ? view(standing, current, now, undoPair) : undefined;
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
  const comparison = await next(rank, standing.groups.get(systems), pending.case_key);
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
