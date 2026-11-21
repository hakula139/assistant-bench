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
  reload_required: 'Reload this page to resume private voting.',
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
  constructor(
    readonly status?: number,
    message = errorMessages.unavailable,
  ) {
    super(message);
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
      throw new NetworkError(response.status);
    }
    throw new ApiError(response.status, parsed.data.error, parsed.data.sitekey);
  }
  const parsed = schema.safeParse(
    response.status === 204 ? undefined : await response.json().catch(() => null),
  );
  if (!parsed.success) {
    throw new NetworkError(response.status);
  }
  return parsed.data;
}

const query = (values: Record<string, string>) => new URLSearchParams(values).toString();

export const listFamilies = () => request(familiesView, 'GET', '/families');
export const admit = (token: string) => request(z.undefined(), 'POST', '/actors', { token });
export const getPair = (family: string) => request(pairState, 'GET', `/pair?${query({ family })}`);
export const skipPair = (pair: string) => request(pairState, 'POST', '/pair/skip', { pair });
export const undoVote = (family: string) =>
  request(z.undefined(), 'POST', '/votes/undo', { family });

const transientVoteError = (error: unknown) => {
  if (error instanceof NetworkError && error.status === undefined) {
    return true;
  }
  return (
    (error instanceof NetworkError ||
      (error instanceof ApiError && error.code === 'unavailable')) &&
    [502, 503, 504].some((status) => status === error.status)
  );
};

/** Replays the original ballot once when a new submission has a transient failure. */
export async function castVote(body: VoteRequest, replay: boolean): Promise<void> {
  const send = () => request(z.undefined(), 'POST', '/votes', body);
  try {
    await send();
  } catch (first) {
    if (!replay || !transientVoteError(first)) {
      throw first;
    }
    try {
      await send();
    } catch (retry) {
      if (retry instanceof ApiError && retry.code === 'rate_limited') {
        throw new NetworkError(retry.status, retry.message);
      }
      // Refusals before recording the replay cannot settle whether the first request committed.
      if (
        retry instanceof ApiError &&
        retry.code !== 'stale_pair' &&
        retry.code !== 'daily_limit' &&
        retry.code !== 'unavailable'
      ) {
        throw first;
      }
      throw retry;
    }
  }
}

/**
 * Asks whether this store's counted votes still match the round's cut.
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
  scroll: z.strictObject({ window: offset, prompt: offset, a: offset, b: offset }),
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
  scroll: { window: 0, prompt: 0, a: 0, b: 0 },
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
