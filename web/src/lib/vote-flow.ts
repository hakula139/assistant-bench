import { z } from 'astro/zod';

import {
  ApiError,
  castVote,
  choiceSchema,
  familyName,
  identifier,
  receiptQuery,
  resolveVote,
  resumeSession,
  resumeSessionRequest,
  startSession,
  startSessionRequest,
  taggedDefects,
  undoVote,
  validateRenderOrigins,
  withdrawRequest,
  type Choice,
  type SessionView,
  type TaggedDefects,
  type VoteAcknowledgment,
} from '@/lib/arena';

const STORAGE_KEY = 'bench-vote-draft';
const draftSchema = z.strictObject({
  family: familyName,
  session_id: identifier,
  lease_id: identifier,
  choice: choiceSchema.nullable(),
  defects: taggedDefects,
  submitted: receiptQuery.nullable(),
  resolved: z.boolean(),
});
const operationSchema = z.discriminatedUnion('kind', [
  z.strictObject({ kind: z.literal('start'), request: startSessionRequest }),
  z.strictObject({ kind: z.literal('resume'), request: resumeSessionRequest }),
]);
const storedSchema = z.strictObject({
  draft: draftSchema.nullable(),
  operation: operationSchema.nullable(),
  withdrawal: withdrawRequest.nullable(),
});
type Stored = z.infer<typeof storedSchema>;
export type Draft = z.infer<typeof draftSchema>;
export const sessionKey = (family: string) => `bench-session:${family}`;
const emptyDefects = (): TaggedDefects => ({ a: [], b: [] });

/** Persists only non-secret command meaning and locators in this tab. */
export class VoteFlow {
  private stored: Stored;

  constructor() {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    let parsed: unknown = null;
    try {
      parsed = raw === null ? null : JSON.parse(raw);
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
    const result = storedSchema.safeParse(parsed);
    this.stored = result.success ? result.data : { draft: null, operation: null, withdrawal: null };
  }

  get draft(): Draft | null {
    return this.stored.draft;
  }

  get family(): string | null {
    return (
      this.draft?.family ??
      (this.stored.operation?.kind === 'start' ? this.stored.operation.request.family : null)
    );
  }

  get withdrawing(): boolean {
    return this.stored.withdrawal !== null;
  }

  get uncertain(): boolean {
    return (
      this.stored.draft?.submitted !== null &&
      this.stored.draft?.submitted !== undefined &&
      !this.stored.draft.resolved
    );
  }

  private save() {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.stored));
  }

  choose(session: SessionView, choice: Choice | null, defects: TaggedDefects) {
    if (session.pair === null || this.uncertain) {
      return;
    }

    const current = this.draft;
    if (
      current?.session_id === session.id &&
      current.lease_id === session.pair.id &&
      current.choice === choice &&
      JSON.stringify(current.defects) === JSON.stringify(defects)
    ) {
      return;
    }
    this.stored.draft = {
      family: session.family.name,
      session_id: session.id,
      lease_id: session.pair.id,
      choice,
      defects,
      submitted: null,
      resolved: false,
    };
    this.save();
  }

  show(session: SessionView): { session: SessionView; changed: boolean } {
    validateRenderOrigins(session);
    localStorage.setItem(sessionKey(session.family.name), session.id);

    if (this.stored.withdrawal?.session_id === session.id) {
      this.stored.withdrawal = null;
      this.save();
    }

    const draft = this.draft;
    const same = draft?.session_id === session.id && draft.lease_id === session.pair?.id;
    const changed = draft !== null && !same;
    if (!same) {
      this.stored.draft =
        session.pair === null
          ? null
          : {
              family: session.family.name,
              session_id: session.id,
              lease_id: session.pair.id,
              choice: null,
              defects: emptyDefects(),
              submitted: null,
              resolved: false,
            };
      this.save();
    }
    return { session, changed };
  }

  async start(family: string): Promise<SessionView> {
    const operation = this.stored.operation;
    const request = startSessionRequest.parse(
      operation?.kind === 'start' && operation.request.family === family
        ? operation.request
        : { command_id: crypto.randomUUID(), family },
    );

    this.stored.operation = { kind: 'start', request };
    this.save();
    const next = await startSession(request);
    this.stored.operation = null;
    this.save();
    return next;
  }

  async resume(session_id: string): Promise<SessionView> {
    const operation = this.stored.operation;
    const request = resumeSessionRequest.parse(
      operation?.kind === 'resume' && operation.request.session_id === session_id
        ? operation.request
        : { command_id: crypto.randomUUID(), session_id },
    );

    this.stored.operation = { kind: 'resume', request };
    this.save();
    const next = await resumeSession(request);
    this.stored.operation = null;
    this.save();
    return next;
  }

  async enter(
    family: string,
    accepted: (receipt: VoteAcknowledgment) => void,
  ): Promise<SessionView> {
    const draft = this.draft;
    if (draft !== null && draft.submitted !== null) {
      try {
        accepted(await resolveVote(draft.submitted));
        this.stored.draft = null;
        this.save();
      } catch (reason) {
        if (!(reason instanceof ApiError && reason.code === 'not_found')) {
          throw reason;
        }
        // A resolved absence permits an explicit retry only on the original lease.
        draft.resolved = true;
        this.save();
      }
    }

    let withdrawn: SessionView | null = null;
    if (this.stored.withdrawal !== null) {
      await undoVote(this.stored.withdrawal);
      withdrawn = await this.resume(this.stored.withdrawal.session_id);
      this.stored.withdrawal = null;
      this.save();
    }
    const id =
      draft?.family === family ? draft.session_id : localStorage.getItem(sessionKey(family));
    if (id !== null) {
      if (withdrawn?.id === id) {
        return withdrawn;
      }
      try {
        return await this.resume(id);
      } catch (reason) {
        if (!(reason instanceof ApiError && reason.code === 'not_found')) {
          throw reason;
        }
        localStorage.removeItem(sessionKey(family));
      }
    }
    return this.start(family);
  }

  async submit(
    session: SessionView,
    choice: Choice,
    defects: TaggedDefects,
  ): Promise<VoteAcknowledgment> {
    if (session.pair === null) {
      throw new ApiError('conflict', 409);
    }
    let request = this.draft?.submitted;
    if (request !== null && request !== undefined) {
      try {
        return await resolveVote(request);
      } catch (reason) {
        if (!(reason instanceof ApiError && reason.code === 'not_found')) {
          throw reason;
        }
      }
    } else {
      request = { command_id: crypto.randomUUID(), lease_id: session.pair.id, choice, defects };
      this.choose(session, choice, defects);
      if (this.stored.draft !== null) {
        this.stored.draft.submitted = request;
        this.stored.draft.resolved = false;
        this.save();
      }
    }
    if (request.lease_id !== session.pair.id) {
      throw new ApiError('conflict', 409);
    }
    return castVote({ ...request, capability: session.pair.capability });
  }

  accepted() {
    this.stored.draft = null;
    this.save();
  }

  async withdraw(session: SessionView): Promise<void> {
    if (session.undo_acknowledgment === null) {
      return;
    }
    const request = this.stored.withdrawal ?? {
      command_id: crypto.randomUUID(),
      session_id: session.id,
      acknowledgment: session.undo_acknowledgment,
    };
    this.stored.withdrawal = request;
    this.save();
    await undoVote(request);
    this.stored.draft = null;
    this.save();
  }
}
