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

const costGap = z.enum(['unpriced', 'unreported']);

const decision = z.strictObject({ leader: z.string(), tied: z.array(z.string()) });

// ── Votes Results ──

const scoreInterval = z.strictObject({ lower: z.number(), upper: z.number() });

const standing = z.strictObject({
  system: z.string(),
  score: z.number(),
  interval: scoreInterval.nullable(),
  rank: z.int(),
  votes: z.int().nonnegative(),
  decisive_votes: z.int().nonnegative(),
  both_bad_rate: z.number().nullable(),
  gate_failure_rate: z.number().nullable(),
  pass_rate: passRate.nullable(),
  mean_cost_usd: z.number().nullable(),
  cost_gap: costGap.nullable(),
  output_tokens: distribution.nullable(),
  latency_sec: distribution.nullable(),
  defects: z.partialRecord(z.enum(defects), z.int()),
});

const headToHead = z.strictObject({
  system: z.string(),
  opponent: z.string(),
  votes: z.int().nonnegative(),
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
  interval_level: z.number().gt(0).lt(1),
});

const votesRules = z.strictObject({
  preliminary_half_width: z.number().positive(),
});

const cutoff = z.strictObject({ id: z.int().nonnegative(), votes: z.int().nonnegative() });
const cutoffs = z
  .strictObject({ production: cutoff.nullable(), private: cutoff.nullable() })
  .refine((value) => value.production !== null || value.private !== null);

const votesResults = z.strictObject({
  scorer: z.literal('votes'),
  method: votesMethod,
  rules: votesRules,
  cutoffs,
  reference: z.string(),
  anchor: z.string(),
  cases: z.int().nonnegative(),
  votes: z.int().nonnegative(),
  effective_votes: z.number().nonnegative(),
  dropped_votes: z.int(),
  voters: z.int(),
  standings: z.array(standing),
  unranked: z.array(z.string()),
  decision: decision.nullable(),
  head_to_head: z.array(headToHead),
  categories: z.array(categoryBoard),
  length: z.number().nullable(),
  split_half_tau: z.number().nullable(),
  public_held_out: z.strictObject({
    public_cases: z.int().nonnegative(),
    held_out_cases: z.int().nonnegative(),
    unavailable: z
      .enum([
        'insufficient_cases',
        'no_common_field',
        'disconnected_field',
        'unidentifiable_field',
        'separated_fit',
      ])
      .nullable(),
    scores: z.array(
      z.strictObject({
        system: z.string(),
        difference: z.number(),
        interval: scoreInterval,
      }),
    ),
  }),
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
  cost_gap: costGap.nullable(),
  latency_sec: distribution.nullable(),
});

const testsCategory = z.strictObject({
  category: z.enum(categories),
  standings: z.array(
    z.strictObject({ system: z.string(), tasks: z.int().min(1), pass_rate: passRate }),
  ),
});

const testsResults = z.strictObject({
  scorer: z.literal('tests'),
  samples: z.int().min(1),
  standings: z.array(testsStanding).min(1),
  categories: z.array(testsCategory),
  decision,
});

const checkRule = z.enum([
  'ellipsis',
  'han-punctuation',
  'curly-quotes',
  'paragraph-lines',
  'list-spacing',
  'tree-comments',
  'forbidden-marker',
]);

const checkersStanding = z.strictObject({
  system: z.string(),
  cases: z.int().min(1),
  responses: z.int().min(1),
  errored: z.int().nonnegative(),
  pass_rate: passRate,
  mean_cost_usd: z.number().nullable(),
  cost_gap: costGap.nullable(),
  latency_sec: distribution.nullable(),
});

const checkersResults = z.strictObject({
  scorer: z.literal('checkers'),
  samples: z.literal(1),
  checker_revision: z.string(),
  standings: z.array(checkersStanding).min(1),
  categories: z.array(
    z.strictObject({
      category: z.enum(categories),
      standings: z.array(
        z.strictObject({ system: z.string(), cases: z.int().min(1), pass_rate: passRate }),
      ),
    }),
  ),
  decision,
  rules: z.array(
    z.strictObject({
      rule: checkRule,
      system: z.string(),
      applicable_cases: z.int().min(1),
      applicable_responses: z.int().min(1),
      passed_responses: z.int().nonnegative(),
      pass_rate: share,
    }),
  ),
  prompt_effects: z.array(
    z.strictObject({
      bare: z.string(),
      prompted: z.string(),
      cases: z.int().min(1),
      delta: z.number().min(-1).max(1),
      interval: scoreInterval.nullable(),
    }),
  ),
});

// ── Report ──

/** Mirrors the pydantic `Report` model, which writes `reports/<family>/<round>.json`. */
export const reportSchema = z
  .strictObject({
    family: z.string().max(FAMILY_NAME_MAX_LENGTH).regex(familyNamePattern),
    round: z.string().regex(/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/),
    closed_at: z.iso.datetime({ offset: true }),
    systems: z.record(z.string().min(1), publicSystem),
    results: z.discriminatedUnion('scorer', [votesResults, testsResults, checkersResults]),
  })
  .superRefine(({ systems, results }, context) => {
    const reject = (message: string) => {
      context.addIssue({ code: 'custom', message });
    };
    const referenced = new Set<string>();
    const standing = new Set(results.standings.map((row) => row.system));

    const validateDecision = ({ leader, tied }: Decision) => {
      if (
        !standing.has(leader) ||
        new Set(tied).size !== tied.length ||
        tied.some((system) => system === leader || !standing.has(system))
      ) {
        reject('The decision must name standing systems once');
      }
    };

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
      results.public_held_out.scores.forEach(({ system }) => referenced.add(system));

      if (results.standings.some((row) => row.cost_gap !== null && row.mean_cost_usd !== null)) {
        reject('A cost gap is present only without a mean cost');
      }

      if (!standing.has(results.anchor)) {
        reject('The anchor must have an overall standing');
      }

      if (
        (results.decision === null) !==
        results.standings.some(({ interval }) => interval === null)
      ) {
        reject('A decision is present exactly when every standing has an interval');
      }

      if (results.decision !== null) {
        validateDecision(results.decision);
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

      if (
        results.standings.some(
          (row) =>
            (row.cost_gap === null) !== (row.mean_cost_usd !== null || row.trials === row.errored),
        )
      ) {
        reject('A cost gap is present exactly when finished trials lack a mean cost');
      }

      validateDecision(results.decision);

      const categories = results.categories.map(({ category }) => category);
      if (
        new Set(categories).size !== categories.length ||
        results.categories.some((board) => board.standings.some((row) => !standing.has(row.system)))
      ) {
        reject('Categories must be unique and list standing systems');
      }
    }

    if (results.scorer === 'checkers') {
      if (
        results.standings.some(
          (row) =>
            (row.cost_gap === null) !==
            (row.mean_cost_usd !== null || row.responses === row.errored),
        )
      ) {
        reject('A cost gap is present exactly when finished trials lack a mean cost');
      }

      validateDecision(results.decision);
      const categories = results.categories.map(({ category }) => category);
      if (
        new Set(categories).size !== categories.length ||
        results.categories.some((board) => board.standings.some((row) => !standing.has(row.system)))
      ) {
        reject('Categories must be unique and list standing systems');
      }
      if (
        results.rules.some((row) => !standing.has(row.system)) ||
        results.prompt_effects.some(
          (pair) => !standing.has(pair.bare) || !standing.has(pair.prompted),
        )
      ) {
        reject('Diagnostics and effects must name standing systems');
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
export type Decision = z.infer<typeof decision>;
export type ScoreInterval = z.infer<typeof scoreInterval>;
export type VotesMethod = z.infer<typeof votesMethod>;
export type VotesRules = z.infer<typeof votesRules>;
export type Cutoffs = z.infer<typeof cutoffs>;
export type Distribution = z.infer<typeof distribution>;
export type Standing = z.infer<typeof standing>;
export type HeadToHead = z.infer<typeof headToHead>;
export type CategoryStanding = z.infer<typeof categoryStanding>;
export type CategoryBoard = z.infer<typeof categoryBoard>;
export type VotesResults = z.infer<typeof votesResults>;
export type PassRate = z.infer<typeof passRate>;
export type CostGap = z.infer<typeof costGap>;
export type TestsStanding = z.infer<typeof testsStanding>;
export type TestsResults = z.infer<typeof testsResults>;
export type CheckersResults = z.infer<typeof checkersResults>;
export type Report = z.infer<typeof reportSchema>;
