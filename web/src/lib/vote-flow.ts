import * as z from 'zod/mini';

import {
  choiceSchema,
  errorView,
  familiesView,
  familyName,
  freshnessView,
  pairId,
  pairState,
  taggedDefects,
  type ErrorCode,
  type VoteRequest,
} from '@/lib/arena';
import type { Cutoffs } from '@/lib/report';

export const errorMessages: Record<ErrorCode, string> = {
  invalid_request: 'The request could not be processed. Reload and try again.',
  forbidden: 'The request could not be processed. Reload and try again.',
  not_found: 'The voting service is unavailable. Try again.',
  too_large: 'The request could not be processed. Reload and try again.',
  admission_required: 'Complete verification to vote.',
  challenge_failed: 'Verification failed. Try again.',
  no_round: 'This family has no outputs available to vote on.',
  stale_pair: 'This pair changed. Review the current outputs and choose again.',
  nothing_to_undo: 'No vote from the last 10 minutes is left to undo.',
  rate_limited: 'Too many requests. Wait a moment, then retry.',
  daily_limit: 'This network has reached its daily limit. Try again tomorrow.',
  unavailable: 'The voting service is unavailable. Try again.',
};

/** A fixed public error code, with the sitekey when verification is required. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ErrorCode,
    readonly sitekey?: string,
  ) {
    super(errorMessages[code]);
  }
}

/** The request may or may not have arrived, so retrying it is the way to find out. */
export class NetworkError extends Error {
  constructor() {
    super(errorMessages.unavailable);
  }
}

/** What to tell the voter about a failure, with `fallback` for one the API did not describe. */
export function errorMessage(error: unknown, fallback = errorMessages.unavailable): string {
  return error instanceof ApiError || error instanceof NetworkError ? error.message : fallback;
}

async function request<T>(
  schema: z.ZodMiniType<T>,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch {
    throw new NetworkError();
  }
  if (!response.ok) {
    const parsed = errorView.safeParse(await response.json().catch(() => null));
    if (!parsed.success) {
      throw new NetworkError();
    }
    throw new ApiError(response.status, parsed.data.error, parsed.data.sitekey);
  }
  const parsed = schema.safeParse(
    response.status === 204 ? undefined : await response.json().catch(() => null),
  );
  if (!parsed.success) {
    throw new NetworkError();
  }
  return parsed.data;
}

const query = (values: Record<string, string>) => new URLSearchParams(values).toString();

export const listFamilies = () => request(familiesView, 'GET', '/families');
export const admit = (token: string) => request(z.undefined(), 'POST', '/actors', { token });
export const getPair = (family: string) => request(pairState, 'GET', `/pair?${query({ family })}`);
export const skipPair = (pair: string) => request(pairState, 'POST', '/pair/skip', { pair });
export const castVote = (body: VoteRequest) => request(z.undefined(), 'POST', '/votes', body);
export const undoVote = (family: string) =>
  request(z.undefined(), 'POST', '/votes/undo', { family });

/**
 * Asks whether the family's counted votes still match what a round closed at these cutoffs read.
 * A null cutoff marks a store the round did not read, so the query leaves it out.
 */
export function checkFreshness(family: string, cutoffs: Cutoffs, signal: AbortSignal) {
  const values: Record<string, string> = { family };
  for (const [store, cutoff] of Object.entries(cutoffs)) {
    if (cutoff !== null) {
      values[store] = String(cutoff.id);
      values[`${store}_votes`] = String(cutoff.votes);
    }
  }
  return request(freshnessView, 'GET', `/freshness?${query(values)}`, undefined, signal);
}

// ── Tab Draft ──

const DRAFT_KEY = 'bench-vote';
const offset = z.number().check(z.nonnegative());
const draftSchema = z.strictObject({
  family: familyName,
  pair: z.nullable(pairId),
  choice: z.nullable(choiceSchema),
  defects: taggedDefects,
  markdown: z.boolean(),
  collapsed: z.boolean(),
  scroll: z.strictObject({ window: offset, a: offset, b: offset }),
});

/** What this tab shows of one pair, kept so a reload shows it again. */
export type Draft = z.infer<typeof draftSchema>;

export const emptyDraft = (family: string, pair: string | null): Draft => ({
  family,
  pair,
  choice: null,
  defects: { a: [], b: [] },
  markdown: false,
  collapsed: false,
  scroll: { window: 0, a: 0, b: 0 },
});

/** The tab's draft, or null after removing one that no longer parses. */
export function readDraft(): Draft | null {
  const raw = sessionStorage.getItem(DRAFT_KEY);
  if (raw === null) {
    return null;
  }
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Removed below like any other invalid draft.
  }
  const result = draftSchema.safeParse(parsed);
  if (!result.success) {
    sessionStorage.removeItem(DRAFT_KEY);
    return null;
  }
  return result.data;
}

export function writeDraft(draft: Draft): void {
  sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
}
