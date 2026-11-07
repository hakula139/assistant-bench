import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

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
import { freshness } from './votes';

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

/** Fills the IP tag's daily cap with votes of another actor. */
async function fillCap(options: { exclusion?: string } = {}) {
  const tag = await ipTag(env.IP_TAG_KEY, TAG_IP);
  const statements = Array.from({ length: VOTES_PER_TAG }, (_, index) =>
    env.DB.prepare(
      `INSERT INTO votes (pair_id, actor_id, family, publication_id, case_key, a_output,
         b_output, a_system, b_system, choice, defects, elapsed_ms, recorded_at, ip_tag, exclusion)
       SELECT ?, 'filler', 'other', publication_id, case_key, output_key, output_key, 'x', ?,
         'a', '{"a": [], "b": []}', 0, ?, ?, ?
       FROM outputs LIMIT 1`,
    ).bind(`filler-${index}`, `y${index}`, Date.now(), tag, options.exclusion ?? null),
  );
  await env.DB.batch([
    env.DB.prepare("INSERT INTO actors (id, created_at) VALUES ('filler', 0)"),
    ...statements,
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

  test('counts only votes inside the window and those not undone toward the cap', async () => {
    await fillCap();
    await env.DB.prepare('UPDATE votes SET recorded_at = ? WHERE pair_id = ?')
      .bind(Date.now() - WINDOW_MS - 1, 'filler-0')
      .run();
    const from = await admitted(TAG_IP);
    const first = await pairOf(from);
    await vote(from, first.pair?.id ?? '');
    await call(from, 'POST', '/votes/undo', { family: WRITING });

    const response = await vote(from, first.pair?.id ?? '');

    expect(response.status).toBe(204);
  });
});

describe('POST /api/votes/undo', () => {
  test('restores the pair with its ID and issue time in place of the pending one', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const issued = await pendingRow(pair?.id ?? '');
    await vote(from, pair?.id ?? '');
    const following = await pairOf(from);

    const response = await call(from, 'POST', '/votes/undo', { family: WRITING });

    expect(response.status).toBe(204);
    expect(await pendingRow(pair?.id ?? '')).toEqual(issued);
    expect(await pendingRow(following.pair?.id ?? '')).toBeNull();
    expect(await storedVotes()).toEqual([]);
    expect(await pairOf(from)).toEqual({
      pair,
      votes: 0,
      remaining: 3,
      undo: false,
    });
    expect(await coverage()).toEqual([]);

    const repeated = await call(from, 'POST', '/votes/undo', { family: WRITING });
    const recorded = await vote(from, pair?.id ?? '');

    expect(repeated.status).toBe(409);
    expect(recorded.status).toBe(204);
    expect(await coverage()).toMatchObject([{ family: WRITING, votes: 1 }]);
  });

  test("walks back through the actor's votes inside the window", async () => {
    const from = await admitted();
    const first = (await pairOf(from)).pair?.id ?? '';
    await vote(from, first);
    const second = (await pairOf(from)).pair?.id ?? '';
    await vote(from, second);

    await call(from, 'POST', '/votes/undo', { family: WRITING });
    const afterOne = await pairOf(from);
    await call(from, 'POST', '/votes/undo', { family: WRITING });
    const afterTwo = await pairOf(from);

    expect(afterOne.pair?.id).toBe(second);
    expect(afterOne.undo).toBe(true);
    expect(afterTwo.pair?.id).toBe(first);
    expect(afterTwo.undo).toBe(false);
  });

  test('passes over a vote the owner excluded, which stays recorded', async () => {
    const from = await admitted();
    const first = (await pairOf(from)).pair?.id ?? '';
    await vote(from, first);
    const second = (await pairOf(from)).pair?.id ?? '';
    await vote(from, second);
    await env.DB.prepare("UPDATE votes SET exclusion = 'trash' WHERE pair_id = ?")
      .bind(second)
      .run();

    const response = await call(from, 'POST', '/votes/undo', { family: WRITING });

    expect(response.status).toBe(204);
    expect((await storedVotes()).map((row) => row.pair_id)).toEqual([second]);
    expect(await pairOf(from)).toMatchObject({ pair: { id: first }, votes: 1, undo: false });
  });

  test.each([
    ['no vote', () => Promise.resolve()],
    [
      'a vote older than the window',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        await vote(from, (await pairOf(from)).pair?.id ?? '');
        await env.DB.prepare('UPDATE votes SET recorded_at = ?')
          .bind(Date.now() - UNDO_MS - 1)
          .run();
      },
    ],
    [
      'a vote the owner excluded',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        await vote(from, (await pairOf(from)).pair?.id ?? '');
        await env.DB.prepare("UPDATE votes SET exclusion = 'trash'").run();
      },
    ],
    [
      'a vote in another family',
      async (from: Awaited<ReturnType<typeof admitted>>) => {
        await vote(from, (await pairOf(from, CODING)).pair?.id ?? '');
      },
    ],
  ])('answers nothing_to_undo after %s', async (_, arrange) => {
    const from = await admitted();
    await arrange(from);

    const response = await call(from, 'POST', '/votes/undo', { family: WRITING });

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'nothing_to_undo' });
  });

  test('restores a pair from a replaced publication, which the next request redraws', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    await vote(from, pair?.id ?? '');
    await publish(WRITING, { only: { 'acme-large': ['x'], 'acme-small': ['y'] } }, 'next');

    await call(from, 'POST', '/votes/undo', { family: WRITING });
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
