import { z } from 'astro/zod';

// ── Shared ──

export const defects = [
  'facts',
  'punctuation',
  'tone',
  'formulaic',
  'length',
  'scope',
  'conventions',
  'complexity',
  'testing',
  'clarity',
] as const;

const publicSystem = z.strictObject({
  display_name: z.string().refine((name) => name.trim().length > 0),
  fingerprint: z.string().nullable(),
});

const distribution = z.strictObject({
  p10: z.number(),
  p50: z.number(),
  p90: z.number(),
});

// ── Votes Results ──

const standing = z.strictObject({
  system: z.string(),
  score: z.number(),
  interval: z.null(),
  rank: z.int(),
  best_rank: z.null(),
  worst_rank: z.null(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  decisive_votes: z.int().nonnegative(),
  both_bad_rate: z.number().nullable(),
  outputs: z.int(),
  gate_failure_rate: z.number().nullable(),
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
  interval: z.null(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  decisive_votes: z.int().nonnegative(),
});

const categoryBoard = z.strictObject({
  category: z.string(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  standings: z.array(categoryStanding),
  unranked: z.array(z.string()),
});

const raterAgreement = z.strictObject({
  repeats: z.int(),
  agreement: z.number().nullable(),
  below_floor: z.boolean(),
});

const votesMethod = z.strictObject({
  name: z.literal('bradley-terry'),
  version: z.literal(1),
  l2_penalty: z.number().positive(),
  points_per_logit: z.number().positive(),
  anchor_score: z.number(),
  human_weights: z.strictObject({
    name: z.literal('unit'),
    version: z.literal(1),
    repeat_handling: z.literal('included'),
  }),
  settled_weights: z.strictObject({
    name: z.literal('sample-pair-share'),
    version: z.literal(1),
  }),
});

const votesRules = z.strictObject({
  preliminary_decisive_votes: z.int().positive(),
  preliminary_decisive_vote_unit: z.literal('raw-votes'),
  rater_agreement_floor: z.number().min(0).max(1),
});

const unavailableInference = z.strictObject({
  status: z.literal('unavailable'),
  target: z.literal('new-cases-and-voters'),
  reason: z.literal('population-method-not-validated'),
});

const votesResults = z.strictObject({
  scorer: z.literal('votes'),
  method: votesMethod,
  rules: votesRules,
  inference: unavailableInference,
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
  separability: z.null(),
  split_half_tau: z.number().nullable(),
  rater_agreement: raterAgreement,
  decision: z.null(),
});

// ── Tests Results ──

const testsStanding = z.strictObject({
  system: z.string(),
  trials: z.int(),
  errored: z.int(),
  pass_rate: z.number(),
  mean_cost_usd: z.number().nullable(),
  p50_latency_sec: z.number().nullable(),
});

const testsResults = z.strictObject({
  scorer: z.literal('tests'),
  standings: z.array(testsStanding),
});

// ── Report ──

/** Mirrors the pydantic `Report` model, which writes `reports/<family>/<round>.json`. */
export const reportSchema = z
  .strictObject({
    family: z.string(),
    round: z.string(),
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

      const aggregates = [
        results,
        ...results.standings,
        ...results.head_to_head,
        ...results.categories.flatMap((board) => [board, ...board.standings]),
      ];

      if (aggregates.some(({ votes, effective_votes }) => effective_votes !== votes)) {
        reject('Unit human weights require effective_votes to equal votes');
      }

      const { agreement, below_floor } = results.rater_agreement;

      if (below_floor !== (agreement !== null && agreement < results.rules.rater_agreement_floor)) {
        reject('Rater agreement must use the recorded floor');
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

export type Defect = (typeof defects)[number];
export type PublicSystem = z.infer<typeof publicSystem>;
export type VotesMethod = z.infer<typeof votesMethod>;
export type VotesRules = z.infer<typeof votesRules>;
export type UnavailableInference = z.infer<typeof unavailableInference>;
export type Distribution = z.infer<typeof distribution>;
export type Standing = z.infer<typeof standing>;
export type HeadToHead = z.infer<typeof headToHead>;
export type CategoryBoard = z.infer<typeof categoryBoard>;
export type VotesResults = z.infer<typeof votesResults>;
export type TestsResults = z.infer<typeof testsResults>;
export type Report = z.infer<typeof reportSchema>;
