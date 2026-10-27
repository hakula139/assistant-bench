// @vitest-environment jsdom
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { z } from 'zod/mini';

import * as arena from '@/lib/arena';
import { COMMAND_ID, makeSession, VOTE_FAMILY } from '@/test/arena';
import { schemaShape } from '@/test/json-schema';

import apiSchemas from '../../../tests/fixtures/arena.schema.json';

const command = { command_id: COMMAND_ID };
const receipt: arena.ReceiptQuery = {
  ...command,
  lease_id: 'lease-1',
  choice: 'a',
  defects: { a: [], b: [] },
};

const contracts = [
  ['text pane', arena.pane, { render: 'text', text: 'Output' }],
  ['diff pane', arena.pane, { render: 'diff', text: '+ added' }],
  ['HTML pane', arena.pane, { render: 'html', url: 'https://render.example/render/signed' }],
  ['family', arena.familyView, VOTE_FAMILY],
  ['pair', arena.pairView, makeSession().pair],
  ['session', arena.sessionView, makeSession()],
  ['status', arena.statusView, { status: 'ready' }],
  [
    'public admission view',
    arena.admissionView,
    { ...command, action: 'bench_admit', sitekey: 'site' },
  ],
  ['public admission', arena.admissionRequest, { ...command, proof: 'challenge-proof' }],
  ['admission replay', arena.admissionReplay, { ...command, proof: null }],
  ['session start', arena.startSessionRequest, { ...command, family: 'fixture' }],
  ['session resume', arena.resumeSessionRequest, { ...command, session_id: 's1' }],
  ['receipt query', arena.receiptQuery, receipt],
  ['vote', arena.voteRequest, { ...receipt, capability: 'pair-capability' }],
  ['acknowledgment', arena.voteAcknowledgment, { acknowledgment: 'ack-1', session_id: 's1' }],
  ['withdrawal', arena.withdrawRequest, { ...command, session_id: 's1', acknowledgment: 'ack-1' }],
  ['freshness query', arena.freshnessRequest, { export_id: 'capture-1' }],
  ['freshness response', arena.freshnessView, { status: 'current' }],
  ['safe error', arena.errorView, { error: 'unavailable' }],
] as const;

describe('arena contracts', () => {
  test.each([
    ['a', true],
    ['family-1', true],
    ['a'.repeat(256), true],
    ['', false],
    ['a\n', false],
    ['../outside', false],
    ['Uppercase', false],
    ['a'.repeat(257), false],
  ] as const)('enforces the native family boundary for %j', (family, valid) => {
    expect(arena.startSessionRequest.safeParse({ ...command, family }).success).toBe(valid);
    expect(arena.familyView.safeParse({ ...VOTE_FAMILY, name: family }).success).toBe(valid);
  });

  test.each(
    Object.entries({
      pane: arena.pane,
      familyView: arena.familyView,
      pairView: arena.pairView,
      sessionView: arena.sessionView,
      statusView: arena.statusView,
      admissionView: arena.admissionView,
      admissionRequest: arena.admissionRequest,
      admissionReplay: arena.admissionReplay,
      startSessionRequest: arena.startSessionRequest,
      resumeSessionRequest: arena.resumeSessionRequest,
      receiptQuery: arena.receiptQuery,
      voteRequest: arena.voteRequest,
      voteAcknowledgment: arena.voteAcknowledgment,
      withdrawRequest: arena.withdrawRequest,
      freshnessRequest: arena.freshnessRequest,
      freshnessView: arena.freshnessView,
      errorView: arena.errorView,
      taggedDefects: arena.taggedDefects,
    }),
  )('matches the native %s schema', (name, schema) => {
    const native: Record<string, Record<string, unknown>> = apiSchemas;

    expect(schemaShape(z.toJSONSchema(schema, { io: 'input' }))).toEqual(schemaShape(native[name]));
  });

  test.each(contracts)(
    'accepts the strict %s contract and rejects extra fields',
    (_, schema, value) => {
      expect(schema.safeParse(value).success).toBe(true);
      expect(schema.safeParse({ ...value, unexpected: true }).success).toBe(false);
    },
  );

  test('accepts the maximum proof length', () => {
    expect(arena.admissionRequest.safeParse({ ...command, proof: 'x'.repeat(2048) }).success).toBe(
      true,
    );
  });

  test('roundtrips all seven selected issues in votes and receipt queries', () => {
    const tagged = { a: VOTE_FAMILY.defects, b: VOTE_FAMILY.defects };
    const query = { ...receipt, defects: tagged };

    expect(arena.receiptQuery.parse(JSON.parse(JSON.stringify(query)))).toEqual(query);
    expect(arena.voteRequest.parse({ ...query, capability: 'pair' })).toEqual({
      ...query,
      capability: 'pair',
    });
  });

  test.each([
    [
      'invalid command',
      arena.startSessionRequest,
      { ...command, command_id: 'not-a-uuid', family: 'f' },
    ],
    ['negative count', arena.sessionView, makeSession({ votes: -1 })],
    ['fractional count', arena.sessionView, makeSession({ remaining: 0.5 })],
    ['unknown choice', arena.receiptQuery, { ...receipt, choice: 'skip' }],
    ['unknown defect', arena.receiptQuery, { ...receipt, defects: { a: ['unknown'], b: [] } }],
    ['retired defect', arena.receiptQuery, { ...receipt, defects: { a: ['punctuation'], b: [] } }],
    [
      'nested defect field',
      arena.receiptQuery,
      { ...receipt, defects: { a: [], b: [], note: 'x' } },
    ],
    ['public note', arena.voteRequest, { ...receipt, capability: 'pair', note: null }],
    ['retired status capability', arena.statusView, { status: 'ready', annotations: false }],
    [
      'retired admission kind',
      arena.admissionView,
      { ...command, kind: 'turnstile', action: 'bench_admit', sitekey: 'site' },
    ],
    ['empty proof', arena.admissionRequest, { ...command, proof: '' }],
    ['long proof', arena.admissionRequest, { ...command, proof: 'x'.repeat(2049) }],
    ['backend detail', arena.errorView, { error: 'unavailable', detail: 'private database error' }],
  ] as const)('rejects %s', (_, schema, value) => {
    expect(schema.safeParse(value).success).toBe(false);
  });
});

describe('arena client', () => {
  test.each([
    ['admission_required', 401],
    ['renewal_required', 403],
    ['conflict', 409],
  ] as const)('preserves the safe %s error code for recovery', async (code, status) => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ error: code }, { status })));

    const response = arena.getStatus();

    await expect(response).rejects.toMatchObject({
      code,
      status,
      message: arena.errorMessages[code],
    });
  });

  test.each([
    ['malformed JSON', '<html>private provider message</html>'],
    ['private detail', JSON.stringify({ detail: 'private database message' })],
    ['unknown code', JSON.stringify({ error: 'private-error' })],
    ['extra detail', JSON.stringify({ error: 'conflict', detail: 'private message' })],
  ])('maps %s to a fixed public error', async (_, body) => {
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(body, { status: 503 })));

    const response = arena.listFamilies();

    await expect(response).rejects.toBeInstanceOf(arena.ApiError);
    await expect(response).rejects.toMatchObject({
      code: 'unavailable',
      message: arena.errorMessages.unavailable,
    });
  });

  test.each([
    ['transport failure', () => Promise.reject(new TypeError('private network detail'))],
    ['malformed success', () => Promise.resolve(new Response('{'))],
    [
      'extra success fields',
      () => Promise.resolve(Response.json({ ...makeSession(), actor_id: 'private' })),
    ],
  ])('keeps %s uncertain without exposing its contents', async (_, fetch) => {
    vi.stubGlobal('fetch', fetch);

    const response = arena.resumeSession({ ...command, session_id: 's1' });

    await expect(response).rejects.toBeInstanceOf(arena.NetworkError);
    await expect(response).rejects.toMatchObject({ message: arena.errorMessages.unavailable });
  });

  test('sends capabilities only in same-origin POST bodies and accepts a separate receipt', async () => {
    const fetch = vi.fn(() =>
      Promise.resolve(Response.json({ acknowledgment: 'ack-1', session_id: 's1' })),
    );
    vi.stubGlobal('fetch', fetch);

    const accepted = await arena.castVote({ ...receipt, capability: 'private-capability' });

    expect(accepted).toEqual({ acknowledgment: 'ack-1', session_id: 's1' });
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/votes', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...receipt, capability: 'private-capability' }),
    });
  });

  test('rejects invalid outgoing fields before sending a request', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);

    expect(() => arena.startSession({ ...command, family: '' })).toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });

  test('acknowledges withdrawal without acquiring another pair', async () => {
    const fetch = vi.fn<(path: string, init?: RequestInit) => Promise<Response>>(() =>
      Promise.resolve(new Response(null, { status: 204 })),
    );
    vi.stubGlobal('fetch', fetch);

    await arena.undoVote({ ...command, session_id: 's1', acknowledgment: 'ack-1' });

    expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/sessions/withdraw']);
  });
});

describe('render origin validation', () => {
  beforeEach(() => {
    document.head.innerHTML = '<meta name="bench-render-origin" content="https://render.example">';
  });

  test.each([
    ['configured renderer', 'https://render.example/render/signed', true],
    ['foreign renderer', 'https://other.example/render/signed', false],
    ['app origin', `${location.origin}/render/signed`, false],
    ['nested path', 'https://render.example/render/signed/extra', false],
    ['query', 'https://render.example/render/signed?token=private', false],
    ['fragment', 'https://render.example/render/signed#private', false],
    ['credentials', 'https://owner:private@render.example/render/signed', false],
    ['invalid URL', '/render/signed', false],
  ])('validates %s', (_, url, allowed) => {
    const session = makeSession();
    if (session.pair === null) {
      throw new Error('Expected a pair');
    }
    session.pair.a = { render: 'html', url };

    if (allowed) {
      expect(arena.validateRenderOrigins(session)).toBe(session);
    } else {
      expect(() => arena.validateRenderOrigins(session)).toThrow();
    }
  });
});
