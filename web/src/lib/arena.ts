import { z } from 'zod/mini';

import { categories } from '@/lib/categories';
import { defects } from '@/lib/defects';
import { FAMILY_NAME_MAX_LENGTH, familyNamePattern } from '@/lib/family-name';

export const identifier = z.string().check(z.minLength(1), z.maxLength(256));
export const familyName = z
  .string()
  .check(z.maxLength(FAMILY_NAME_MAX_LENGTH), z.regex(familyNamePattern));

const command = { command_id: z.uuid() };
const capability = z.string().check(z.minLength(1), z.maxLength(2048));
export const choiceSchema = z.enum(['a', 'b', 'tie', 'both_bad']);
export const taggedDefects = z.strictObject({
  a: z._default(z.array(z.enum(defects)), []),
  b: z._default(z.array(z.enum(defects)), []),
});
export const pane = z.discriminatedUnion('render', [
  z.strictObject({ render: z.literal('text'), text: z.string() }),
  z.strictObject({ render: z.literal('html'), url: z.string() }),
  z.strictObject({ render: z.literal('diff'), text: z.string() }),
]);
export const pairView = z.strictObject({
  id: identifier,
  capability,
  instruction: z.string(),
  checklist: z.array(z.string()),
  category: z.nullable(z.enum(categories)),
  a: pane,
  b: pane,
});
export const familyView = z.strictObject({
  name: familyName,
  aspect: z.nullable(z.string()),
  display_name: z.string(),
  defects: z.array(z.enum(defects)),
});
export const sessionView = z.strictObject({
  id: identifier,
  family: familyView,
  votes: z.int().check(z.nonnegative()),
  remaining: z.int().check(z.nonnegative()),
  undo_acknowledgment: z.nullable(identifier),
  pair: z.nullable(pairView),
});
export const statusView = z.strictObject({
  status: z.enum(['admission_required', 'renewal_required', 'ready']),
});
export const admissionView = z.strictObject({
  ...command,
  action: z.enum(['bench_admit', 'bench_renew']),
  sitekey: identifier,
});
export const admissionRequest = z.strictObject({ ...command, proof: capability });
export const admissionReplay = z.strictObject({
  ...command,
  proof: z.optional(z.nullable(capability)),
});
export const startSessionRequest = z.strictObject({ ...command, family: familyName });
export const resumeSessionRequest = z.strictObject({ ...command, session_id: identifier });
export const receiptQuery = z.strictObject({
  ...command,
  lease_id: identifier,
  choice: choiceSchema,
  defects: z._default(taggedDefects, { a: [], b: [] }),
});
export const voteRequest = z.extend(receiptQuery, { capability });
export const voteAcknowledgment = z.strictObject({
  acknowledgment: identifier,
  session_id: identifier,
});
export const withdrawRequest = z.strictObject({
  ...command,
  session_id: identifier,
  acknowledgment: identifier,
});
export const freshnessView = z.strictObject({ status: z.enum(['current', 'stale', 'unknown']) });
export const errorView = z.strictObject({
  error: z.enum([
    'invalid_request',
    'admission_required',
    'renewal_required',
    'challenge_rejected',
    'rate_limited',
    'not_found',
    'conflict',
    'unavailable',
  ]),
});

export type Choice = z.infer<typeof choiceSchema>;
export type TaggedDefects = z.infer<typeof taggedDefects>;
export type FamilyView = z.infer<typeof familyView>;
export type Pane = z.infer<typeof pane>;
export type PairView = z.infer<typeof pairView>;
export type SessionView = z.infer<typeof sessionView>;
export type AdmissionView = z.infer<typeof admissionView>;
export type ReceiptQuery = z.infer<typeof receiptQuery>;
export type VoteAcknowledgment = z.infer<typeof voteAcknowledgment>;
export type Freshness = z.infer<typeof freshnessView>['status'];
export type ErrorCode = z.infer<typeof errorView>['error'];

export const errorMessages: Record<ErrorCode, string> = {
  invalid_request: 'The request could not be processed. Reload and try again.',
  admission_required: 'Complete verification to vote.',
  renewal_required: 'Verify again to continue. Your choice is saved in this tab.',
  challenge_rejected: 'The challenge was rejected. Complete a new challenge.',
  rate_limited: 'Too many requests. Wait a moment, then retry.',
  not_found: 'That item is no longer available.',
  conflict: 'This pair changed. Review the current outputs and choose again.',
  unavailable: 'The voting service is unavailable. Try again.',
};

/** A fixed public error code, without backend or provider details. */
export class ApiError extends Error {
  constructor(
    readonly code: ErrorCode,
    readonly status: number,
  ) {
    super(errorMessages[code]);
  }
}

/** The response is uncertain and the exact command can be retried. */
export class NetworkError extends Error {
  constructor() {
    super(errorMessages.unavailable);
  }
}

export async function request<T>(
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
    throw new ApiError(parsed.success ? parsed.data.error : 'unavailable', response.status);
  }
  const parsed = schema.safeParse(
    response.status === 204 ? undefined : await response.json().catch(() => null),
  );
  if (!parsed.success) {
    throw new NetworkError();
  }
  return parsed.data;
}

export const getStatus = () => request(statusView, 'GET', '/status');
export const getAdmission = () => request(admissionView, 'GET', '/admission');
export const admit = (body: z.infer<typeof admissionRequest>) =>
  request(statusView, 'POST', '/admission', admissionRequest.parse(body));
export const replayAdmission = (body: z.infer<typeof admissionReplay>) =>
  request(statusView, 'POST', '/admission/replay', admissionReplay.parse(body));
export const listFamilies = () => request(z.array(familyView), 'GET', '/families');
export const startSession = (body: z.infer<typeof startSessionRequest>) =>
  request(sessionView, 'POST', '/sessions', startSessionRequest.parse(body));
export const resumeSession = (body: z.infer<typeof resumeSessionRequest>) =>
  request(sessionView, 'POST', '/sessions/resume', resumeSessionRequest.parse(body));
export const castVote = (body: z.infer<typeof voteRequest>) =>
  request(voteAcknowledgment, 'POST', '/votes', voteRequest.parse(body));
export const resolveVote = (body: ReceiptQuery) =>
  request(voteAcknowledgment, 'POST', '/votes/resolve', receiptQuery.parse(body));
export const undoVote = (body: z.infer<typeof withdrawRequest>) =>
  request(z.undefined(), 'POST', '/sessions/withdraw', withdrawRequest.parse(body));
/** Asks whether a round closed at these cutoffs still holds every counted vote of the family. */
export function checkFreshness(
  family: string,
  cutoffs: Record<'production' | 'local', number | null>,
  signal: AbortSignal,
) {
  const query = new URLSearchParams({ family });
  for (const [store, cutoff] of Object.entries(cutoffs)) {
    if (cutoff !== null) {
      query.set(store, String(cutoff));
    }
  }
  return request(freshnessView, 'GET', `/freshness?${query.toString()}`, undefined, signal);
}

/** HTML capabilities may load only from the runtime's configured, separate renderer. */
export function validateRenderOrigins(session: SessionView): SessionView {
  for (const output of session.pair === null ? [] : [session.pair.a, session.pair.b]) {
    if (output.render !== 'html') {
      continue;
    }
    const configured = document.querySelector<HTMLMetaElement>(
      'meta[name="bench-render-origin"]',
    )?.content;
    const url = new URL(output.url);
    if (
      configured === undefined ||
      url.origin !== configured ||
      url.origin === location.origin ||
      !/^\/render\/[^/]+$/.test(url.pathname) ||
      url.search !== '' ||
      url.hash !== '' ||
      url.username !== '' ||
      url.password !== ''
    ) {
      throw new NetworkError();
    }
  }
  return session;
}
