import { z } from 'astro/zod';

import { defects, type Defect } from '@/lib/report';

// The vote page's policy forbids eval, which Zod otherwise probes for to compile its parsers.
z.config({ jitless: true });

// ── API Models ──

export type Choice = 'a' | 'b' | 'tie' | 'both_bad';

const pane = z.discriminatedUnion('render', [
  z.object({ render: z.literal('text'), text: z.string() }),
  z.object({ render: z.literal('html'), url: z.url() }),
  z.object({ render: z.literal('diff'), text: z.string() }),
]);

const pairView = z.object({
  token: z.string(),
  instruction: z.string(),
  checklist: z.array(z.string()),
  a: pane,
  b: pane,
});

const familyView = z.object({
  name: z.string(),
  aspect: z.string().nullable(),
  display_name: z.string(),
  defects: z.array(z.enum(defects)),
});

const sessionView = z.object({
  id: z.string(),
  family: familyView,
  votes: z.int().nonnegative(),
  remaining: z.int().nonnegative(),
  pair: pairView.nullable(),
});

export type TaggedDefects = Record<'a' | 'b', Defect[]>;
export type FamilyView = z.infer<typeof familyView>;
export type Pane = z.infer<typeof pane>;
export type PairView = z.infer<typeof pairView>;
export type SessionView = z.infer<typeof sessionView>;

// ── Client ──

/** An error response from the vote API, carrying its HTTP status. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** No response from the vote API, as when `bench serve` has stopped. */
export class NetworkError extends Error {}

async function request<T>(
  schema: z.ZodType<T>,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).catch((reason: unknown) => {
    throw new NetworkError(`no response from ${path}`, { cause: reason });
  });

  if (!response.ok) {
    const detail = z
      .object({ detail: z.string() })
      .safeParse(await response.json().catch(() => null));
    throw new ApiError(
      detail.success ? detail.data.detail : `HTTP ${response.status}`,
      response.status,
    );
  }

  return schema.parse(await response.json());
}

export const listFamilies = () => request(z.array(familyView), 'GET', '/families');

export const startSession = (family: string) =>
  request(sessionView, 'POST', '/sessions', { family });

export const resumeSession = (id: string) =>
  request(sessionView, 'POST', `/sessions/${encodeURIComponent(id)}/pair`);

export const castVote = (
  token: string,
  vote: { choice: Choice; defects: TaggedDefects; note: string | null },
) => request(sessionView, 'POST', `/pairs/${encodeURIComponent(token)}/vote`, vote);

export const undoVote = (id: string) =>
  request(sessionView, 'POST', `/sessions/${encodeURIComponent(id)}/undo`);
