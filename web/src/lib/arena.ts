import * as z from 'zod/mini';

import { categories } from '@/lib/categories';
import { defects } from '@/lib/defects';
import { FAMILY_NAME_MAX_LENGTH, familyNamePattern } from '@/lib/family-name';

export const familyName = z
  .string()
  .check(z.maxLength(FAMILY_NAME_MAX_LENGTH), z.regex(familyNamePattern));
export const pairId = z.uuid();
export const UNDO_HEADER = 'Arena-Undo';
export const UNDO_NONE = 'none';
export const choiceSchema = z.enum(['a', 'b', 'tie', 'both_bad']);
const defectSchema = z.enum(defects);
export const taggedDefects = z.strictObject({
  a: z.array(defectSchema),
  b: z.array(defectSchema),
});
const count = z.int().check(z.nonnegative());

// ── Requests ──

export const admissionRequest = z.strictObject({
  token: z.string().check(z.minLength(1), z.maxLength(2048)),
});
export const familyQuery = z.strictObject({ family: familyName });
export const skipRequest = z.strictObject({ pair: pairId });
export const previewRequest = z.strictObject({ pair: pairId, side: z.enum(['a', 'b']) });
export const voteRequest = z.strictObject({
  pair: pairId,
  choice: choiceSchema,
  defects: taggedDefects,
});
export const undoRequest = z.strictObject({ family: familyName, pair: pairId });
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
const pairFields = {
  id: pairId,
  instruction: z.string(),
  checklist: z.array(z.string()),
  category: z.enum(categories),
  defects: z.array(defectSchema),
};
export const pairView = z.discriminatedUnion('render', [
  z.strictObject({ ...pairFields, render: z.enum(['text', 'diff']), a: z.string(), b: z.string() }),
  z.strictObject({ ...pairFields, render: z.literal('html') }),
]);
export const pairState = z.strictObject({
  pair: z.nullable(pairView),
  votes: count,
  remaining: count,
  undo: z.boolean(),
});
const errorCode = z.enum([
  'invalid_request',
  'forbidden',
  'not_found',
  'too_large',
  'admission_required',
  'reload_required',
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
export type UndoRequest = z.infer<typeof undoRequest>;
export type FamilyView = z.infer<typeof familiesView>['families'][number];
export type PairView = z.infer<typeof pairView>;
export type PairState = z.infer<typeof pairState>;
export type ErrorCode = z.infer<typeof errorCode>;
export type ErrorView = z.infer<typeof errorView>;
