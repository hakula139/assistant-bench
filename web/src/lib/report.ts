import { z } from 'astro/zod';

// ── Shared ──

export const flags = ['meaning', 'punctuation', 'register', 'script', 'length'] as const;

export const flagLabels: Record<Flag, string> = {
  meaning: 'Meaning changed',
  punctuation: 'Punctuation',
  register: 'Register',
  script: 'Wrong script',
  length: 'Too long',
};

const interval = z.strictObject({
  lower: z.number(),
  upper: z.number(),
});

const distribution = z.strictObject({
  p10: z.number(),
  p50: z.number(),
  p90: z.number(),
});

// ── Votes Results ──

const standing = z.strictObject({
  system: z.string(),
  fingerprint: z.string().nullable(),
  score: z.number(),
  interval,
  rank: z.int(),
  best_rank: z.int(),
  worst_rank: z.int(),
  preliminary: z.boolean(),
  votes: z.int(),
  decisive_votes: z.int(),
  both_bad_rate: z.number(),
  outputs: z.int(),
  gate_failure_rate: z.number().nullable(),
  mean_cost_usd: z.number().nullable(),
  p50_latency_sec: z.number().nullable(),
  output_tokens: distribution.nullable(),
  latency_sec: distribution.nullable(),
  flags: z.partialRecord(z.enum(flags), z.int()),
});

const headToHead = z.strictObject({
  system: z.string(),
  opponent: z.string(),
  votes: z.int(),
  wins: z.int(),
  losses: z.int(),
  ties: z.int(),
  both_bad: z.int(),
  win_rate: z.number(),
});

const categoryStanding = z.strictObject({
  system: z.string(),
  score: z.number(),
  interval,
  votes: z.int(),
  preliminary: z.boolean(),
});

const categoryBoard = z.strictObject({
  category: z.string(),
  votes: z.int(),
  standings: z.array(categoryStanding),
});

const raterAgreement = z.strictObject({
  repeats: z.int(),
  agreement: z.number().nullable(),
  below_floor: z.boolean(),
});

const decision = z.strictObject({
  leader: z.string(),
  separated: z.boolean(),
  tie_break: z.enum(['score', 'cost', 'latency']),
});

const votesResults = z.strictObject({
  scorer: z.literal('votes'),
  reference: z.string(),
  votes: z.int(),
  sessions: z.int(),
  standings: z.array(standing),
  head_to_head: z.array(headToHead),
  categories: z.array(categoryBoard),
  position: z.number(),
  length: z.number().nullable(),
  separability: z.number().nullable(),
  split_half_tau: z.number().nullable(),
  rater_agreement: raterAgreement,
  decision,
});

// ── Tests Results ──

const testsStanding = z.strictObject({
  system: z.string(),
  fingerprint: z.string().nullable(),
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
export const reportSchema = z.strictObject({
  family: z.string(),
  round: z.string(),
  closed_at: z.iso.datetime({ offset: true }),
  results: z.discriminatedUnion('scorer', [votesResults, testsResults]),
});

export type Report = z.infer<typeof reportSchema>;
export type Flag = (typeof flags)[number];
export type Interval = z.infer<typeof interval>;
export type Distribution = z.infer<typeof distribution>;
export type VotesResults = z.infer<typeof votesResults>;
export type Standing = z.infer<typeof standing>;
export type CategoryBoard = z.infer<typeof categoryBoard>;
export type HeadToHead = z.infer<typeof headToHead>;
export type Decision = z.infer<typeof decision>;
export type TestsResults = z.infer<typeof testsResults>;
