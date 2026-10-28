import { z } from 'astro/zod';

import { categories } from '@/lib/categories';
import { defects } from '@/lib/defects';
import { FAMILY_NAME_MAX_LENGTH, familyNamePattern } from '@/lib/family-name';

// ── Shared ──

const publicSystem = z.strictObject({
  display_name: z.string().refine((name) => name.trim().length > 0),
  fingerprint: z.string().nullable(),
});

const distribution = z.strictObject({
  p10: z.number(),
  p50: z.number(),
  p90: z.number(),
});

const share = z.number().min(0).max(1);

const passRate = z.strictObject({ rate: share, lower: share, upper: share });

// ── Votes Results ──

const standing = z.strictObject({
  system: z.string(),
  score: z.number(),
  rank: z.int(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  decisive_votes: z.int().nonnegative(),
  both_bad_rate: z.number().nullable(),
  outputs: z.int(),
  gate_failure_rate: z.number().nullable(),
  pass_rate: passRate.nullable(),
  mean_cost_usd: z.number().nullable(),
  p50_latency_sec: z.number().nullable(),
  output_tokens: distribution.nullable(),
  latency_sec: distribution.nullable(),
  defects: z.partialRecord(z.enum(defects), z.int()),
});

const headToHead = z.strictObject({
  system: z.string(),
  opponent: z.string(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  wins: z.int(),
  losses: z.int(),
  ties: z.int(),
  both_bad: z.int(),
  win_rate: z.number(),
});

const categoryStanding = z.strictObject({
  system: z.string(),
  score: z.number(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  decisive_votes: z.int().nonnegative(),
});

const categoryBoard = z.strictObject({
  category: z.enum(categories),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  standings: z.array(categoryStanding),
  unranked: z.array(z.string()),
});

const votesMethod = z.strictObject({
  l2_penalty: z.number().positive(),
  points_per_logit: z.number().positive(),
  anchor_score: z.number(),
  actor_cap: z.int().positive(),
});

const votesRules = z.strictObject({
  preliminary_decisive_votes: z.int().positive(),
});

const provenance = z.strictObject({
  schema_version: z.literal(1),
  export_id: z.string().min(1),
  export_digest: z.string().min(1),
  namespace: z.string().min(1),
  captured_at: z.iso.datetime({ offset: true }),
  event_cutoff: z.int().nonnegative(),
  receipt_cutoff: z.int().nonnegative(),
  family_revision: z.int().nonnegative(),
  analysis_sequence: z.int().nonnegative(),
  evaluation_id: z.string().min(1),
  publication_id: z.string().min(1),
  policy_id: z.string().min(1),
  sampling_point_snapshot_id: z.string().min(1),
  status: z.literal('ledger'),
  weighted_evidence_digest: z.string().min(1),
  fitted_point_snapshot_id: z.string().min(1),
});

const votesResults = z.strictObject({
  scorer: z.literal('votes'),
  method: votesMethod,
  rules: votesRules,
  provenance,
  reference: z.string(),
  anchor: z.string(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  dropped_votes: z.int(),
  sessions: z.int(),
  standings: z.array(standing),
  unranked: z.array(z.string()),
  head_to_head: z.array(headToHead),
  categories: z.array(categoryBoard),
  position: z.number().nullable(),
  length: z.number().nullable(),
  split_half_tau: z.number().nullable(),
});

// ── Tests Results ──

const testsStanding = z.strictObject({
  system: z.string(),
  tasks: z.int().min(1),
  trials: z.int().min(1),
  errored: z.int().nonnegative(),
  pass_rate: passRate,
  pass_hat_k: share.nullable(),
  mean_cost_usd: z.number().nullable(),
  p50_latency_sec: z.number().nullable(),
});

const testsCategory = z.strictObject({
  category: z.string(),
  standings: z.array(
    z.strictObject({ system: z.string(), tasks: z.int().min(1), pass_rate: passRate }),
  ),
});

const testsResults = z.strictObject({
  scorer: z.literal('tests'),
  samples: z.int().min(1),
  standings: z.array(testsStanding).min(1),
  categories: z.array(testsCategory),
  decision: z.strictObject({ leader: z.string(), tied: z.array(z.string()) }),
});

// ── Report ──

/** Mirrors the pydantic `Report` model, which writes `reports/<family>/<round>.json`. */
export const reportSchema = z
  .strictObject({
    family: z.string().max(FAMILY_NAME_MAX_LENGTH).regex(familyNamePattern),
    round: z.string().regex(/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/),
    closed_at: z.iso.datetime({ offset: true }),
    systems: z.record(z.string().min(1), publicSystem),
    results: z.discriminatedUnion('scorer', [votesResults, testsResults]),
  })
  .superRefine(({ systems, results }, context) => {
    const reject = (message: string) => {
      context.addIssue({ code: 'custom', message });
    };
    const referenced = new Set<string>();

    const membership = (standings: { system: string }[], unranked: string[] = []) => {
      const ids = [...standings.map(({ system }) => system), ...unranked];
      if (new Set(ids).size !== ids.length) {
        reject('Standing and unranked memberships must be distinct');
      }

      ids.forEach((id) => referenced.add(id));
    };

    membership(results.standings, results.scorer === 'votes' ? results.unranked : []);

    if (results.scorer === 'votes') {
      referenced.add(results.reference);
      referenced.add(results.anchor);

      if (!results.standings.some(({ system }) => system === results.anchor)) {
        reject('The anchor must have an overall standing');
      }

      results.head_to_head.forEach(({ system, opponent }) => {
        referenced.add(system);
        referenced.add(opponent);
      });
      results.categories.forEach(({ standings, unranked }) => {
        membership(standings, unranked);
      });
    }

    if (results.scorer === 'tests') {
      if (results.standings.some((row) => (row.pass_hat_k === null) !== (results.samples === 1))) {
        reject('pass^k is present exactly when tasks run several times');
      }

      const standing = new Set(results.standings.map((row) => row.system));
      const { leader, tied } = results.decision;
      if (
        !standing.has(leader) ||
        new Set(tied).size !== tied.length ||
        tied.some((system) => system === leader || !standing.has(system))
      ) {
        reject('The decision must name standing systems once');
      }

      const categories = results.categories.map(({ category }) => category);
      if (
        new Set(categories).size !== categories.length ||
        results.categories.some((board) => board.standings.some((row) => !standing.has(row.system)))
      ) {
        reject('Categories must be unique and list standing systems');
      }
    }

    const ids = Object.keys(systems);

    if (
      ids.some((id) => id.trim().length === 0) ||
      [...referenced].some((id) => id.trim().length === 0)
    ) {
      reject('System IDs must contain non-whitespace text');
    }

    if (ids.length !== referenced.size || ids.some((id) => !referenced.has(id))) {
      reject('Public system metadata must exactly match referenced systems');
    }

    const names = Object.values(systems).map(({ display_name }) => display_name);

    if (new Set(names).size !== names.length) {
      reject('Public display names must be unique');
    }
  });

export type PublicSystem = z.infer<typeof publicSystem>;
export type VotesMethod = z.infer<typeof votesMethod>;
export type VotesRules = z.infer<typeof votesRules>;
export type Distribution = z.infer<typeof distribution>;
export type Standing = z.infer<typeof standing>;
export type HeadToHead = z.infer<typeof headToHead>;
export type CategoryStanding = z.infer<typeof categoryStanding>;
export type CategoryBoard = z.infer<typeof categoryBoard>;
export type VotesResults = z.infer<typeof votesResults>;
export type PassRate = z.infer<typeof passRate>;
export type TestsStanding = z.infer<typeof testsStanding>;
export type TestsResults = z.infer<typeof testsResults>;
export type Report = z.infer<typeof reportSchema>;
