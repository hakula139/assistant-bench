import * as z from 'zod/mini';

import { categories } from '@/lib/categories';
import { defects } from '@/lib/defects';
import { FAMILY_NAME_MAX_LENGTH, familyNamePattern } from '@/lib/family-name';

export const familyName = z
  .string()
  .check(z.maxLength(FAMILY_NAME_MAX_LENGTH), z.regex(familyNamePattern));
export const pairId = z.uuid();
export const choiceSchema = z.enum(['a', 'b', 'tie', 'both_bad']);
export const defectSchema = z.enum(defects);
export const taggedDefects = z.strictObject({
  a: z.array(defectSchema),
  b: z.array(defectSchema),
});
const count = z.int().check(z.nonnegative());
/** A vote ID or a vote count as a query parameter. */
const queryCount = z.string().check(z.regex(/^\d{1,15}$/));

// ── Requests ──

export const admissionRequest = z.strictObject({
  token: z.string().check(z.minLength(1), z.maxLength(2048)),
});
export const familyQuery = z.strictObject({ family: familyName });
export const skipRequest = z.strictObject({ pair: pairId });
export const voteRequest = z.strictObject({
  pair: pairId,
  choice: choiceSchema,
  defects: taggedDefects,
});
export const undoRequest = familyQuery;
/** Each store's cutoff with the counted votes the round read up to it, or neither. */
export const freshnessQuery = z
  .strictObject({
    family: familyName,
    production: z.optional(queryCount),
    production_votes: z.optional(queryCount),
    local: z.optional(queryCount),
    local_votes: z.optional(queryCount),
  })
  .check(
    z.refine(
      (query) =>
        (query.production === undefined) === (query.production_votes === undefined) &&
        (query.local === undefined) === (query.local_votes === undefined),
    ),
  );

// ── Responses ──

export const familiesView = z.strictObject({
  families: z.array(
    z.strictObject({
      family: familyName,
      display_name: z.string(),
      aspect: z.nullable(z.string()),
    }),
  ),
});
export const pairView = z.strictObject({
  id: pairId,
  instruction: z.string(),
  checklist: z.array(z.string()),
  category: z.enum(categories),
  render: z.enum(['text', 'diff']),
  a: z.string(),
  b: z.string(),
  defects: z.array(defectSchema),
});
export const pairState = z.strictObject({
  pair: z.nullable(pairView),
  votes: count,
  remaining: count,
  undo: z.boolean(),
});
export const freshnessView = z.strictObject({ status: z.enum(['current', 'stale', 'unknown']) });
export const errorCode = z.enum([
  'invalid_request',
  'forbidden',
  'not_found',
  'too_large',
  'admission_required',
  'challenge_failed',
  'no_round',
  'stale_pair',
  'nothing_to_undo',
  'rate_limited',
  'daily_limit',
  'unavailable',
]);
export const errorView = z.strictObject({
  error: errorCode,
  sitekey: z.optional(z.string()),
});

export type Choice = z.infer<typeof choiceSchema>;
export type TaggedDefects = z.infer<typeof taggedDefects>;
export type VoteRequest = z.infer<typeof voteRequest>;
export type FreshnessQuery = z.infer<typeof freshnessQuery>;
export type FamilyView = z.infer<typeof familiesView>['families'][number];
export type PairView = z.infer<typeof pairView>;
export type PairState = z.infer<typeof pairState>;
export type Freshness = z.infer<typeof freshnessView>['status'];
export type ErrorCode = z.infer<typeof errorCode>;
export type ErrorView = z.infer<typeof errorView>;
