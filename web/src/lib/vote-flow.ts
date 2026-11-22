import * as z from 'zod/mini';

import {
  choiceSchema,
  errorView,
  familiesView,
  familyName,
  pairId,
  pairState,
  taggedDefects,
  UNDO_HEADER,
  UNDO_NONE,
  type ErrorCode,
  type PairState,
  type UndoRequest,
  type VoteRequest,
} from '@/lib/arena';

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
  nothing_to_undo: 'That vote is no longer available to undo.',
  rate_limited: 'Too many requests. Wait a moment, then retry.',
  daily_limit:
    'This network has added too many new browsers in the past 24 hours. Try again later.',
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

/** The service gave no usable response, so a write may have taken effect. */
export class NetworkError extends Error {
  constructor(readonly transient = false) {
    super(errorMessages.unavailable);
  }
}

const isGatewayFailure = (status: number) => status === 502 || status === 503 || status === 504;

export function isTransientTransport(error: unknown): boolean {
  return (
    (error instanceof NetworkError && error.transient) ||
    (error instanceof ApiError && error.code === 'unavailable' && isGatewayFailure(error.status))
  );
}

/** What to tell the voter about a failure, with `fallback` for one the API did not describe. */
export function errorMessage(error: unknown, fallback = errorMessages.unavailable): string {
  return error instanceof ApiError || error instanceof NetworkError ? error.message : fallback;
}

async function send(
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch {
    throw new NetworkError(!signal?.aborted);
  }
  if (!response.ok) {
    const parsed = errorView.safeParse(await response.json().catch(() => null));
    if (!parsed.success) {
      throw new NetworkError(isGatewayFailure(response.status));
    }
    throw new ApiError(response.status, parsed.data.error, parsed.data.sitekey);
  }
  return response;
}

async function decode<T>(schema: z.ZodMiniType<T>, response: Response): Promise<T> {
  const value: unknown =
    response.status === 204
      ? undefined
      : await response.json().catch((error: unknown) => {
          throw new NetworkError(error instanceof TypeError);
        });
  const parsed = schema.safeParse(value);
  if (!parsed.success) {
    throw new NetworkError();
  }
  return parsed.data;
}

async function request<T>(
  schema: z.ZodMiniType<T>,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return decode(schema, await send(method, path, body, signal));
}

/** The displayed pair state and the latest eligible vote, when the Worker supports targeted Undo. */
export interface PairSnapshot extends PairState {
  undoPair: string | null | undefined;
}

async function requestPair(
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<PairSnapshot> {
  const response = await send(method, path, body, signal);
  const state = await decode(pairState, response);
  const token = response.headers.get(UNDO_HEADER);
  const undoPair = token === UNDO_NONE ? null : pairId.safeParse(token).data;
  return { ...state, undoPair };
}

const query = (values: Record<string, string>) => new URLSearchParams(values).toString();

export const listFamilies = () => request(familiesView, 'GET', '/families');
export const admit = (token: string) => request(z.undefined(), 'POST', '/actors', { token });

const PAIR_RETRY_MS = 400;
function waitForPairRetry(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const cancel = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', cancel);
      resolve();
    }, PAIR_RETRY_MS);
    signal.addEventListener('abort', cancel, { once: true });
  });
}

export async function getPair(family: string, signal: AbortSignal) {
  const path = `/pair?${query({ family })}`;
  const read = () => requestPair('GET', path, undefined, signal);
  try {
    return await read();
  } catch (error) {
    if (signal.aborted || !isTransientTransport(error)) {
      throw error;
    }
  }

  await waitForPairRetry(signal);
  signal.throwIfAborted();
  return read();
}

export const skipPair = (pair: string) => requestPair('POST', '/pair/skip', { pair });
export const castVote = (body: VoteRequest) => request(z.undefined(), 'POST', '/votes', body);
export const confirmVote = (body: VoteRequest) =>
  request(z.undefined(), 'POST', '/votes/confirm', body);
export const undoVote = (body: UndoRequest) => request(z.undefined(), 'POST', '/votes/undo', body);

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
