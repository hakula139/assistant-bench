// @vitest-environment jsdom
import { beforeEach, describe, expect, test, vi } from 'vitest';

import { ApiError, NetworkError, voteRequest } from '@/lib/arena';
import { sessionKey, VoteFlow } from '@/lib/vote-flow';
import { requestBody as body, makeSession, VOTE_FAMILY } from '@/test/arena';

type Fetch = (path: string, init?: RequestInit) => Promise<Response>;
const defects = { a: [], b: [] };
const acknowledgment = { acknowledgment: 'ack-1', session_id: 's1' };

function reply(value: unknown, status = 200): Promise<Response> {
  return Promise.resolve(Response.json(value, { status }));
}

function opened() {
  const session = makeSession();
  const flow = new VoteFlow();
  flow.show(session);
  return { flow, session };
}

describe('VoteFlow', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  test.each(['other\n', '../outside', 'a'.repeat(257)])(
    'preserves the original unresolved vote through invalid family entry %j and reload',
    async (family) => {
      const { flow, session } = opened();
      const fetch = vi.fn<Fetch>((path) =>
        path === '/api/votes'
          ? Promise.reject(new TypeError('Connection lost'))
          : reply({ error: 'not_found' }, 404),
      );
      vi.stubGlobal('fetch', fetch);
      await expect(flow.submit(session, 'a', defects)).rejects.toBeInstanceOf(NetworkError);
      const original = flow.draft?.submitted;
      expect(original).toBeDefined();

      await expect(new VoteFlow().enter(family, vi.fn())).rejects.toThrow();

      const restored = new VoteFlow();
      expect(restored.draft?.submitted).toEqual(original);
      expect(restored.draft?.family).toBe(VOTE_FAMILY.name);
      expect(fetch.mock.calls.filter(([path]) => path === '/api/votes')).toHaveLength(1);
    },
  );

  test.each(['', 'a'.repeat(257)])(
    'preserves a valid draft when a saved resume locator is invalid: %j',
    async (sessionId) => {
      const { flow, session } = opened();
      flow.choose(session, 'b', defects);
      const original = flow.draft;
      const fetch = vi.fn<Fetch>();
      vi.stubGlobal('fetch', fetch);

      await expect(flow.resume(sessionId)).rejects.toThrow();

      expect(new VoteFlow().draft).toEqual(original);
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  test('retains the same lease draft through renewal without persisting content or capabilities', () => {
    const { flow, session } = opened();
    flow.choose(session, 'b', { a: ['tone'], b: [] });
    const renewed = makeSession();
    if (renewed.pair === null) {
      throw new Error('Expected a pair');
    }
    renewed.pair.capability = 'fresh-private-capability';

    const restored = new VoteFlow();
    const shown = restored.show(renewed);

    expect(shown.changed).toBe(false);
    expect(restored.draft).toMatchObject({ choice: 'b', defects: { a: ['tone'], b: [] } });
    expect(localStorage.getItem(sessionKey(VOTE_FAMILY.name))).toBe('s1');
    const stored = sessionStorage.getItem('bench-vote-draft');
    expect(stored).not.toMatch(/capability|Output A|Output B|Compare the two outputs/);
  });

  test.each(['{', '{"unexpected":"private-capability"}'])(
    'discards malformed saved meaning %s',
    (raw) => {
      sessionStorage.setItem('bench-vote-draft', raw);

      const flow = new VoteFlow();
      flow.show(makeSession());

      expect(flow.uncertain).toBe(false);
      expect(flow.draft?.choice).toBeNull();
      expect(sessionStorage.getItem('bench-vote-draft')).not.toContain('private-capability');
    },
  );

  test.each(['start', 'resume'] as const)(
    'replays the same %s command after a lost response and reload',
    async (operation) => {
      const fetch = vi
        .fn<Fetch>()
        .mockRejectedValueOnce(new TypeError('Connection lost'))
        .mockResolvedValueOnce(Response.json(makeSession()));
      vi.stubGlobal('fetch', fetch);
      const locator = operation === 'start' ? VOTE_FAMILY.name : 's1';

      await expect(new VoteFlow()[operation](locator)).rejects.toBeInstanceOf(NetworkError);
      const next = await new VoteFlow()[operation](locator);

      expect(next.id).toBe('s1');
      expect(fetch.mock.calls.map(([path]) => path)).toEqual([
        operation === 'start' ? '/api/sessions' : '/api/sessions/resume',
        operation === 'start' ? '/api/sessions' : '/api/sessions/resume',
      ]);
      expect(body(fetch.mock.calls[1][1])).toEqual(body(fetch.mock.calls[0][1]));
    },
  );

  test.each(['lost-response', 'renewal'] as const)(
    'resolves an accepted %s vote before a failed next draw',
    async (failure) => {
      const { flow, session } = opened();
      let available = false;
      const fetch = vi.fn<Fetch>((path) => {
        if (path === '/api/votes') {
          if (failure === 'renewal') {
            return reply({ error: 'renewal_required' }, 403);
          }
          throw new TypeError('Connection lost');
        }
        if (path === '/api/votes/resolve') {
          return reply(acknowledgment);
        }
        return available
          ? reply(makeSession({ votes: 1, undo_acknowledgment: 'ack-1' }))
          : reply({ error: 'unavailable' }, 503);
      });
      vi.stubGlobal('fetch', fetch);
      await expect(flow.submit(session, 'a', defects)).rejects.toBeInstanceOf(
        failure === 'renewal' ? ApiError : NetworkError,
      );
      flow.choose(session, 'b', defects);
      expect(flow.draft?.choice).toBe('a');
      const accepted = vi.fn();

      await expect(new VoteFlow().enter(VOTE_FAMILY.name, accepted)).rejects.toMatchObject({
        code: 'unavailable',
      });

      expect(accepted).toHaveBeenCalledExactlyOnceWith(acknowledgment);
      expect(fetch.mock.calls.map(([path]) => path)).toEqual([
        '/api/votes',
        '/api/votes/resolve',
        '/api/sessions/resume',
      ]);
      expect(sessionStorage.getItem('bench-vote-draft')).not.toContain('private-pair-capability');

      available = true;
      const resumed = await new VoteFlow().enter(VOTE_FAMILY.name, accepted);

      expect(resumed.votes).toBe(1);
      expect(fetch.mock.calls.filter(([path]) => path === '/api/votes')).toHaveLength(1);
      expect(accepted).toHaveBeenCalledTimes(1);
    },
  );

  test.each(['a', 'b'] as const)(
    'requires explicit %s resubmission after authoritative receipt absence',
    async (choice) => {
      const { flow, session } = opened();
      const initial = vi.fn<Fetch>().mockRejectedValue(new TypeError('Connection lost'));
      vi.stubGlobal('fetch', initial);
      await expect(flow.submit(session, 'a', defects)).rejects.toBeInstanceOf(NetworkError);
      const original = voteRequest.parse(body(initial.mock.calls[0][1]));
      const renewed = makeSession();
      if (renewed.pair === null) {
        throw new Error('Expected a pair');
      }
      renewed.pair.capability = 'renewed-capability';
      const fetch = vi.fn<Fetch>((path) => {
        if (path === '/api/votes/resolve') {
          return reply({ error: 'not_found' }, 404);
        }
        if (path === '/api/sessions/resume') {
          return reply(renewed);
        }
        return reply(acknowledgment);
      });
      vi.stubGlobal('fetch', fetch);
      const recovered = new VoteFlow();
      const accepted = vi.fn();

      const next = await recovered.enter(VOTE_FAMILY.name, accepted);
      recovered.show(next);

      expect(recovered.draft?.choice).toBe('a');
      expect(recovered.uncertain).toBe(false);
      expect(accepted).not.toHaveBeenCalled();
      expect(fetch.mock.calls.some(([path]) => path === '/api/votes')).toBe(false);

      recovered.choose(next, choice, defects);
      await recovered.submit(next, choice, defects);

      const sent = fetch.mock.calls.find(([path]) => path === '/api/votes');
      const request = voteRequest.parse(body(sent?.[1]));
      expect(request).toMatchObject({
        lease_id: original.lease_id,
        choice,
        capability: 'renewed-capability',
      });
      expect(request.command_id === original.command_id).toBe(choice === 'a');
    },
  );

  test.each([true, false])(
    'preserves the original family command before another family when receipt resolution is %s',
    async (resolved) => {
      const { flow, session } = opened();
      vi.stubGlobal('fetch', vi.fn<Fetch>().mockRejectedValue(new TypeError('Connection lost')));
      await expect(flow.submit(session, 'a', defects)).rejects.toBeInstanceOf(NetworkError);
      const original = flow.draft?.submitted;
      const other = makeSession({ id: 's2', family: { ...VOTE_FAMILY, name: 'other' } });
      const fetch = vi.fn<Fetch>((path) => {
        if (path === '/api/votes/resolve') {
          return resolved ? reply(acknowledgment) : reply({ error: 'unavailable' }, 503);
        }
        return reply(other);
      });
      vi.stubGlobal('fetch', fetch);
      const restored = new VoteFlow();
      const accepted = vi.fn();

      const result = restored.enter('other', accepted);

      if (resolved) {
        await expect(result).resolves.toMatchObject({ family: { name: 'other' } });
        expect(accepted).toHaveBeenCalledExactlyOnceWith(acknowledgment);
        expect(fetch.mock.calls[0][0]).toBe('/api/votes/resolve');
      } else {
        await expect(result).rejects.toMatchObject({ code: 'unavailable' });
        expect(restored.draft?.submitted).toEqual(original);
        expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/votes/resolve']);
      }
    },
  );

  test('replays the exact acknowledged withdrawal after the next draw fails', async () => {
    const session = makeSession({ votes: 1, undo_acknowledgment: 'ack-1' });
    const flow = new VoteFlow();
    flow.show(session);
    let available = false;
    const fetch = vi.fn<Fetch>((path) => {
      if (path === '/api/sessions/withdraw') {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (!available) {
        throw new TypeError('Connection lost');
      }
      return reply(makeSession());
    });
    vi.stubGlobal('fetch', fetch);

    await flow.withdraw(session);
    await expect(flow.resume(session.id)).rejects.toBeInstanceOf(NetworkError);

    expect(flow.withdrawing).toBe(true);
    available = true;
    const restored = new VoteFlow();
    await restored.enter(VOTE_FAMILY.name, vi.fn());

    const withdrawals = fetch.mock.calls.filter(([path]) => path === '/api/sessions/withdraw');
    expect(body(withdrawals[1][1])).toEqual(body(withdrawals[0][1]));
    expect(body(withdrawals[1][1])).toMatchObject({ acknowledgment: 'ack-1', session_id: 's1' });
    expect(restored.withdrawing).toBe(false);
  });

  test('replays the requested family acquisition after resolving another family receipt', async () => {
    const { flow, session } = opened();
    vi.stubGlobal('fetch', vi.fn<Fetch>().mockRejectedValue(new TypeError('Connection lost')));
    await expect(flow.submit(session, 'a', defects)).rejects.toBeInstanceOf(NetworkError);
    const other = makeSession({ id: 's2', family: { ...VOTE_FAMILY, name: 'other' } });
    let available = false;
    const fetch = vi.fn<Fetch>((path) => {
      if (path === '/api/votes/resolve') {
        return reply(acknowledgment);
      }
      return available ? reply(other) : reply({ error: 'unavailable' }, 503);
    });
    vi.stubGlobal('fetch', fetch);
    const accepted = vi.fn();

    await expect(new VoteFlow().enter('other', accepted)).rejects.toMatchObject({
      code: 'unavailable',
    });
    expect(accepted).toHaveBeenCalledExactlyOnceWith(acknowledgment);
    available = true;
    const restored = new VoteFlow();
    const next = await restored.enter('other', accepted);
    restored.show(next);

    const starts = fetch.mock.calls.filter(([path]) => path === '/api/sessions');
    expect(body(starts[1][1])).toEqual(body(starts[0][1]));
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      '/api/votes/resolve',
      '/api/sessions',
      '/api/sessions',
    ]);
    expect(accepted).toHaveBeenCalledTimes(1);
    expect(next).toMatchObject({ id: 's2', votes: 0, undo_acknowledgment: null });
    expect(restored.draft).toMatchObject({ family: 'other', choice: null, submitted: null });
  });
});
