import { env } from 'cloudflare:workers';
import { describe, expect, test, vi } from 'vitest';

import { UNDO_HEADER, UNDO_NONE } from '@/lib/arena';

import { ipTag } from './actors';
import { UNDO_MS, VOTES_PER_TAG, WINDOW_MS } from './limits';
import {
  actorOf,
  admitted,
  browser,
  call,
  CODING,
  pairOf,
  pendingRow,
  publish,
  seedVote,
  vote,
  WRITING,
} from './test/support';
import { freshness, record, undo } from './votes';

const TAG_IP = '198.51.100.9';

async function storedVotes() {
  const { results } = await env.DB.prepare(
    'SELECT pair_id, case_key, a_system, b_system, choice, defects, elapsed_ms FROM votes ORDER BY id',
  ).all();
  return results;
}

async function coverage() {
  const { results } = await env.DB.prepare(
    `SELECT family, low_system, high_system, votes FROM pair_vote_counts
     WHERE votes > 0 ORDER BY family, low_system, high_system`,
  ).all();
  const expected = await env.DB.prepare(
    `SELECT family, min(a_system, b_system) AS low_system, max(a_system, b_system) AS high_system,
       count(*) AS votes FROM votes WHERE exclusion IS NULL
     GROUP BY family, low_system, high_system ORDER BY family, low_system, high_system`,
  ).all();
  expect(results).toEqual(expected.results);
  return results;
}

/** Seeds recent votes from another actor on the same network. */
async function fillCap({
  count = VOTES_PER_TAG,
  exclusion,
}: { count?: number; exclusion?: string } = {}) {
  const tag = await ipTag(env.IP_TAG_KEY, TAG_IP);
  await env.DB.batch([
    env.DB.prepare("INSERT INTO actors (id, created_at) VALUES ('filler', 0)"),
    env.DB.prepare(
      `WITH RECURSIVE slots(i) AS (VALUES (0) UNION ALL SELECT i + 1 FROM slots WHERE i + 1 < ?)
       INSERT INTO votes (pair_id, actor_id, family, publication_id, case_key, a_output,
         b_output, a_system, b_system, choice, defects, elapsed_ms, recorded_at, ip_tag, exclusion)
       SELECT 'filler-' || i, 'filler', 'other', publication_id, case_key, output_key, output_key,
         'x', 'y' || i,
         'a', '{"a": [], "b": []}', 0, ?, ?, ?
       FROM slots CROSS JOIN (SELECT publication_id, case_key, output_key FROM outputs LIMIT 1)`,
    ).bind(count, Date.now(), tag, exclusion ?? null),
  ]);
}

describe('POST /api/votes', () => {
  test("records the systems from the outputs and the Worker's own elapsed time", async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const id = pair?.id ?? '';
    const pending = await pendingRow(id);
    await env.DB.prepare('UPDATE pairs SET issued_at = issued_at - 5000').run();

    const response = await call(from, 'POST', '/votes', {
      pair: id,
      choice: 'b',
      defects: { a: ['tone', 'facts', 'tone'], b: [] },
    });

    expect(response.status).toBe(204);
    const [stored] = await storedVotes();
    expect(stored).toMatchObject({
      pair_id: id,
      case_key: pending?.case_key,
      a_system: pending?.a_system,
      b_system: pending?.b_system,
      choice: 'b',
      defects: '{"a":["facts","tone"],"b":[]}',
    });
    expect(stored.elapsed_ms).toBeGreaterThanOrEqual(5000);
    expect(stored.elapsed_ms).toBeLessThan(60_000);
    expect(await pendingRow(id)).toBeNull();
    expect(await coverage()).toMatchObject([{ family: WRITING, votes: 1 }]);
  });

  test('answers a repeated identical vote with 204 and records it once', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const body = { pair: pair?.id, choice: 'tie', defects: { a: ['facts'], b: [] } };
    await call(from, 'POST', '/votes', body);

    const response = await call(from, 'POST', '/votes', body);

    expect(response.status).toBe(204);
    expect(await storedVotes()).toHaveLength(1);
    expect(await coverage()).toMatchObject([{ family: WRITING, votes: 1 }]);
  });

  test('answers two identical votes sent at once with 204 each and records one', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const body = { pair: pair?.id, choice: 'tie', defects: { a: ['facts'], b: [] } };

    const responses = await Promise.all([
      call(from, 'POST', '/votes', body),
      call(from, 'POST', '/votes', body),
    ]);

    expect(responses.map(({ status }) => status)).toEqual([204, 204]);
    expect(await storedVotes()).toHaveLength(1);
  });

  test.each([
    ['another choice', { choice: 'b', defects: { a: ['facts'], b: [] } }],
    ['other tags', { choice: 'tie', defects: { a: [], b: ['facts'] } }],
  ])('answers a retry with %s as stale_pair', async (_, change) => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    await call(from, 'POST', '/votes', {
      pair: pair?.id,
      choice: 'tie',
      defects: { a: ['facts'], b: [] },
    });

    const response = await call(from, 'POST', '/votes', { pair: pair?.id, ...change });

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'stale_pair' });
    await coverage();
  });

  test.each([
    ['an unknown pair', () => Promise.resolve(crypto.randomUUID())],
    [
      "another actor's pair",
      async () => (await pairOf(await admitted('192.0.2.200'))).pair?.id ?? '',
    ],
    [
      'a pair whose publication was replaced',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        const { pair } = await pairOf(from);
        await publish(WRITING, { only: { 'acme-large': ['x'], 'acme-small': ['y'] } }, 'next');
        return pair?.id ?? '';
      },
    ],
    [
      'an already voted comparison',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        const { pair } = await pairOf(from);
        const row = await pendingRow(pair?.id ?? '');
        await seedVote(WRITING, row?.case_key ?? '', [row?.a_system ?? '', row?.b_system ?? ''], {
          actor: await actorOf(from),
        });
        return pair?.id ?? '';
      },
    ],
  ])('answers a vote on %s as stale_pair', async (_, arrange) => {
    const from = await admitted();
    const pair = await arrange(from);

    const response = await vote(from, pair);

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'stale_pair' });
    await coverage();
  });

  test.each([
    ['a tag the family does not list', { a: ['testing'], b: [] }],
    ['an unknown tag', { a: [], b: ['spelling'] }],
  ])('refuses %s', async (_, defects) => {
    const from = await admitted();
    const { pair } = await pairOf(from);

    const response = await call(from, 'POST', '/votes', { pair: pair?.id, choice: 'a', defects });

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: 'invalid_request' });
    expect(await storedVotes()).toEqual([]);
  });

  test.each([
    ['counted votes', {}],
    ['excluded votes, which refund nothing', { exclusion: 'trash' }],
  ])("stops the network's votes at its daily cap of %s and keeps the pair", async (_, options) => {
    await fillCap(options);
    const from = await admitted(TAG_IP);
    const { pair } = await pairOf(from);

    const response = await vote(from, pair?.id ?? '');

    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({ error: 'daily_limit' });
    expect(await pendingRow(pair?.id ?? '')).not.toBeNull();
    await coverage();
  });

  test('shares the final slot across families and accepts an identical retry at the cap', async () => {
    await fillCap({ count: 1_999 });
    const writing = await admitted(TAG_IP);
    const coding = await admitted(TAG_IP);
    const writingPair = (await pairOf(writing, WRITING)).pair?.id ?? '';
    const codingPair = (await pairOf(coding, CODING)).pair?.id ?? '';

    const [writingVote, codingVote] = await Promise.all([
      vote(writing, writingPair),
      vote(coding, codingPair),
    ]);

    expect([writingVote.status, codingVote.status].sort()).toEqual([204, 429]);
    const [winner, recorded, pending] = writingVote.ok
      ? [writing, writingPair, codingPair]
      : [coding, codingPair, writingPair];
    const refused = writingVote.ok ? codingVote : writingVote;
    expect(await refused.json()).toEqual({ error: 'daily_limit' });
    expect(await pendingRow(pending)).not.toBeNull();
    expect((await vote(winner, recorded)).status).toBe(204);
    const conflicting = await vote(winner, recorded, 'b');
    expect(conflicting.status).toBe(409);
    expect(await conflicting.json()).toEqual({ error: 'stale_pair' });
    expect(await env.DB.prepare('SELECT count(*) AS votes FROM votes').first('votes')).toBe(2_000);
  });

  test('keeps a capped request retryable when another browser immediately undoes a vote', async () => {
    await fillCap({ count: VOTES_PER_TAG - 1 });
    const earlier = await admitted(TAG_IP);
    const earlierPair = (await pairOf(earlier)).pair?.id ?? '';
    expect((await vote(earlier, earlierPair)).status).toBe(204);

    const waiting = await admitted(TAG_IP);
    const pair = (await pairOf(waiting)).pair?.id ?? '';
    const earlierActor = await actorOf(earlier);
    const waitingActor = await actorOf(waiting);
    const tag = await ipTag(env.IP_TAG_KEY, TAG_IP);
    const now = Date.now();
    const batch = env.DB.batch.bind(env.DB);
    vi.spyOn(env.DB, 'batch').mockImplementationOnce(async (statements) => {
      const results = await batch(statements);
      await undo(env.DB, earlierActor, { family: WRITING, pair: earlierPair }, now);
      return results;
    });

    await expect(
      record(env.DB, waitingActor, tag, { pair, choice: 'a', defects: { a: [], b: [] } }, now),
    ).rejects.toMatchObject({ code: 'daily_limit' });
    expect(await pendingRow(pair)).not.toBeNull();
    expect((await vote(waiting, pair)).status).toBe(204);
  });

  test('uses a slot freed by the rolling window and then by undo', async () => {
    await fillCap();
    await env.DB.prepare('UPDATE votes SET recorded_at = ? WHERE pair_id = ?')
      .bind(Date.now() - WINDOW_MS - 1, 'filler-0')
      .run();
    const from = await admitted(TAG_IP);
    const first = await pairOf(from);
    expect((await vote(from, first.pair?.id ?? '')).status).toBe(204);
    const undo = await call(from, 'POST', '/votes/undo', { family: WRITING, pair: first.pair?.id });
    expect(undo.status).toBe(204);

    const restored = await pairOf(from);
    const response = await vote(from, restored.pair?.id ?? '');

    expect(response.status).toBe(204);
  });
});

describe('POST /api/votes/undo', () => {
  test('restores the same comparison and issue time and refuses a late vote on its old ID', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const issued = await pendingRow(pair?.id ?? '');
    await vote(from, pair?.id ?? '');
    const following = await pairOf(from);
    const request = { family: WRITING, pair: pair?.id };

    const response = await call(from, 'POST', '/votes/undo', request);

    expect(response.status).toBe(204);
    const restored = await pairOf(from);
    expect(restored.pair?.id).not.toBe(pair?.id);
    expect(restored.pair).toEqual({ ...pair, id: restored.pair?.id });
    expect(await pendingRow(restored.pair?.id ?? '')).toEqual(issued);
    expect(await pendingRow(pair?.id ?? '')).toBeNull();
    expect(await pendingRow(following.pair?.id ?? '')).toBeNull();
    expect(await storedVotes()).toEqual([]);
    expect(restored).toMatchObject({ votes: 0, remaining: 3, undo: false });
    expect(await coverage()).toEqual([]);

    const replay = await vote(from, pair?.id ?? '');
    expect(replay.status).toBe(409);
    expect(await replay.json()).toEqual({ error: 'stale_pair' });
    expect(await storedVotes()).toEqual([]);

    const recorded = await vote(from, restored.pair?.id ?? '', 'b');
    expect(recorded.status).toBe(204);
    expect(await storedVotes()).toMatchObject([{ pair_id: restored.pair?.id, choice: 'b' }]);
    expect(await coverage()).toMatchObject([{ family: WRITING, votes: 1 }]);
  });

  test('withdraws a target once when two tabs send Undo and refreshes the earlier target', async () => {
    const from = await admitted();
    const first = (await pairOf(from)).pair;
    await vote(from, first?.id ?? '');
    const second = (await pairOf(from)).pair;
    await vote(from, second?.id ?? '');

    const view = await call(from, 'GET', `/pair?family=${WRITING}`);
    expect(view.headers.get(UNDO_HEADER)).toBe(second?.id);
    const pending = (await pairOf(from)).pair;
    const skipped = await call(from, 'POST', '/pair/skip', { pair: pending?.id });
    expect(skipped.status).toBe(200);
    expect(skipped.headers.get(UNDO_HEADER)).toBe(second?.id);
    const older = await call(from, 'POST', '/votes/undo', { family: WRITING, pair: first?.id });
    expect(older.status).toBe(409);

    const request = { family: WRITING, pair: second?.id };
    const repeated = await Promise.all([
      call(from, 'POST', '/votes/undo', request),
      call(from, 'POST', '/votes/undo', request),
    ]);
    expect(repeated.map(({ status }) => status).sort()).toEqual([204, 409]);
    expect((await storedVotes()).map((row) => row.pair_id)).toEqual([first?.id]);

    const refreshed = await call(from, 'GET', `/pair?family=${WRITING}`);
    expect(refreshed.headers.get(UNDO_HEADER)).toBe(first?.id);
    expect(await refreshed.json()).toMatchObject({ pair: { instruction: second?.instruction } });
    const earlier = await call(from, 'POST', '/votes/undo', { family: WRITING, pair: first?.id });
    expect(earlier.status).toBe(204);
    const empty = await call(from, 'GET', `/pair?family=${WRITING}`);
    expect(empty.headers.get(UNDO_HEADER)).toBe(UNDO_NONE);
    expect(await empty.json()).toMatchObject({ pair: { instruction: first?.instruction } });
  });

  test('passes over a vote the owner excluded, which stays recorded', async () => {
    const from = await admitted();
    const first = (await pairOf(from)).pair;
    await vote(from, first?.id ?? '');
    const second = (await pairOf(from)).pair?.id ?? '';
    await vote(from, second);
    await env.DB.prepare("UPDATE votes SET exclusion = 'trash' WHERE pair_id = ?")
      .bind(second)
      .run();

    const visible = await call(from, 'GET', `/pair?family=${WRITING}`);
    expect(visible.headers.get(UNDO_HEADER)).toBe(first?.id);
    const response = await call(from, 'POST', '/votes/undo', { family: WRITING, pair: first?.id });

    expect(response.status).toBe(204);
    expect((await storedVotes()).map((row) => row.pair_id)).toEqual([second]);
    expect(await pairOf(from)).toMatchObject({
      pair: { instruction: first?.instruction },
      votes: 1,
      undo: false,
    });
  });

  test.each([
    ['no vote', () => Promise.resolve(crypto.randomUUID())],
    [
      'a vote older than the window',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        const pair = (await pairOf(from)).pair?.id ?? '';
        await vote(from, pair);
        await env.DB.prepare('UPDATE votes SET recorded_at = ?')
          .bind(Date.now() - UNDO_MS - 1)
          .run();
        return pair;
      },
    ],
    [
      'a vote the owner excluded',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        const pair = (await pairOf(from)).pair?.id ?? '';
        await vote(from, pair);
        await env.DB.prepare("UPDATE votes SET exclusion = 'trash'").run();
        return pair;
      },
    ],
    [
      'a vote in another family',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        const pair = (await pairOf(from, CODING)).pair?.id ?? '';
        await vote(from, pair);
        return pair;
      },
    ],
    [
      'a vote by another actor',
      async () => {
        const other = await admitted('192.0.2.25');
        const pair = (await pairOf(other)).pair?.id ?? '';
        await vote(other, pair);
        return pair;
      },
    ],
  ])('answers nothing_to_undo after %s', async (_, arrange) => {
    const from = await admitted();
    const pair = await arrange(from);

    const response = await call(from, 'POST', '/votes/undo', { family: WRITING, pair });

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'nothing_to_undo' });
  });

  test('restores a pair from a replaced publication, which the next request redraws', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    await vote(from, pair?.id ?? '');
    await publish(WRITING, { only: { 'acme-large': ['x'], 'acme-small': ['y'] } }, 'next');

    await call(from, 'POST', '/votes/undo', { family: WRITING, pair: pair?.id });
    const state = await pairOf(from);

    expect(state.pair?.id).not.toBe(pair?.id);
    expect(state.pair?.instruction).toBe('Instruction only');
  });
});

describe('freshness', () => {
  const caseKey = '32be94015839ac0131d6874ffcc11c79f78e8059790f678d80fa7008a1286c22';
  const systems = ['acme-large', 'acme-small'] as const;

  /** The IDs of every vote, and the counted votes, as `bench close` reads them. */
  async function read() {
    const { results } = await env.DB.prepare(
      'SELECT id, exclusion IS NULL AS counted FROM votes ORDER BY id',
    ).all<{ id: number; counted: number }>();
    return { ids: results.map(({ id }) => id), counted: results.filter((row) => row.counted) };
  }

  test.each([
    ['current while the counted votes match', 'production', 0, true, 'current'],
    ['stale with a counted vote after the cutoff', 'production', 1, true, 'stale'],
    ['unknown on local while its counted votes match', 'local', 0, true, 'unknown'],
    ['unknown on local after its cutoff', 'local', 1, true, 'unknown'],
    ['unknown without a production cutoff', 'production', 0, false, 'unknown'],
    ['unknown on staging', 'staging', 0, true, 'unknown'],
  ] as const)('answers %s', async (_, store, later, cut, expected) => {
    await seedVote(WRITING, caseKey, systems);
    await seedVote(WRITING, caseKey, systems);
    const { ids } = await read();
    const cutoff = ids[ids.length - 1 - later];

    const status = await freshness(env.DB, store, {
      family: WRITING,
      ...(cut
        ? {
            [store === 'local' ? 'private' : 'production']: String(cutoff),
            [store === 'local' ? 'private_votes' : 'production_votes']: String(ids.length - later),
          }
        : {}),
    });

    expect(status).toBe(expected);
  });

  test.each([
    ['an exclusion', undefined, 'trash'],
    ['a restore', 'trash', null],
  ] as const)('turns stale after %s at or below the cutoff', async (_, before, after) => {
    await seedVote(WRITING, caseKey, systems, { exclusion: before });
    await seedVote(WRITING, caseKey, systems);
    const { ids, counted } = await read();
    const query = {
      family: WRITING,
      production: String(ids[1]),
      production_votes: String(counted.length),
    };
    expect(await freshness(env.DB, 'production', query)).toBe('current');

    await env.DB.prepare('UPDATE votes SET exclusion = ? WHERE id = ?').bind(after, ids[0]).run();

    expect(await freshness(env.DB, 'production', query)).toBe('stale');
  });

  test('leaves excluded votes after the cutoff out', async () => {
    await seedVote(WRITING, caseKey, systems, { exclusion: 'trash' });

    const response = await call(
      browser(),
      'GET',
      `/freshness?family=${WRITING}&production=0&production_votes=0`,
    );

    expect(await response.json()).toEqual({ status: 'current' });
  });

  test('turns stale when a newer counted vote replaces one at the cutoff', async () => {
    await seedVote(WRITING, caseKey, systems);
    const { ids } = await read();
    const query = { family: WRITING, production: String(ids[0]), production_votes: '1' };
    await env.DB.prepare("UPDATE votes SET exclusion = 'trash' WHERE id = ?").bind(ids[0]).run();
    await seedVote(WRITING, caseKey, systems);

    const status = await freshness(env.DB, 'production', query);

    expect(status).toBe('stale');
    expect(await coverage()).toEqual([
      { family: WRITING, low_system: 'acme-large', high_system: 'acme-small', votes: 1 },
    ]);
  });

  test('counts reversed systems together and isolates families through moderation and deletion', async () => {
    await seedVote(WRITING, caseKey, systems);
    await seedVote(WRITING, caseKey, [systems[1], systems[0]]);
    await publish('other-writing', { other: { 'acme-large': ['a'], 'acme-small': ['b'] } });
    await seedVote('other-writing', 'other', systems, { exclusion: 'trash' });
    await seedVote(WRITING, caseKey, systems, { exclusion: 'trash' });
    const { ids } = await read();
    expect(await coverage()).toEqual([
      { family: WRITING, low_system: 'acme-large', high_system: 'acme-small', votes: 2 },
    ]);
    await env.DB.prepare("UPDATE votes SET exclusion = NULL WHERE family = 'other-writing'").run();
    expect(await coverage()).toEqual([
      { family: 'other-writing', low_system: 'acme-large', high_system: 'acme-small', votes: 1 },
      { family: WRITING, low_system: 'acme-large', high_system: 'acme-small', votes: 2 },
    ]);

    for (const [sql, count] of [
      ["UPDATE votes SET exclusion = 'trash' WHERE family = ?", 0],
      ["UPDATE votes SET exclusion = 'another reason' WHERE family = ?", 0],
      ['UPDATE votes SET exclusion = NULL WHERE family = ?', 3],
      ['UPDATE votes SET exclusion = NULL WHERE family = ?', 3],
      ['UPDATE votes SET ip_tag = NULL WHERE family = ?', 3],
      ['DELETE FROM votes WHERE family = ?', 0],
    ] as const) {
      await env.DB.prepare(sql).bind(WRITING).run();

      const rows = await coverage();

      expect(rows).toEqual([
        { family: 'other-writing', low_system: 'acme-large', high_system: 'acme-small', votes: 1 },
        ...(count === 0
          ? []
          : [
              {
                family: WRITING,
                low_system: 'acme-large',
                high_system: 'acme-small',
                votes: count,
              },
            ]),
      ]);
    }
    expect(
      await freshness(env.DB, 'production', {
        family: WRITING,
        production: String(ids.at(-1)),
        production_votes: '0',
      }),
    ).toBe('current');
  });

  test('reuses an answer for the same parameters, in any order, for a minute', async () => {
    await seedVote(WRITING, caseKey, systems);
    const { ids } = await read();
    const from = browser();
    const ask = async (query: string) =>
      (await (await call(from, 'GET', `/freshness?${query}`)).json<{ status: string }>()).status;
    const cut = `production=${ids[0]}&production_votes=1`;
    expect(await ask(`family=${WRITING}&${cut}`)).toBe('current');

    await seedVote(WRITING, caseKey, systems);

    expect(await ask(`${cut}&family=${WRITING}`)).toBe('current');
    expect(await ask(`family=${WRITING}&production=${ids[0]}&production_votes=2`)).toBe('stale');
  });

  test('rejects a cutoff without its counted votes', async () => {
    const response = await call(browser(), 'GET', `/freshness?family=${WRITING}&production=0`);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: 'invalid_request' });
  });
});
