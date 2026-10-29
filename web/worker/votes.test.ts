import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

import { ipTag } from './actors';
import { UNDO_MS, VOTES_PER_TAG, WINDOW_MS } from './limits';
import {
  actorOf,
  admitted,
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

/** Fills the IP tag's daily cap but `missing` with votes of other actors. */
async function fillCap(missing: number, options: { exclusion?: string } = {}) {
  const tag = await ipTag(env.IP_TAG_KEY, TAG_IP);
  const statements = Array.from({ length: VOTES_PER_TAG - missing }, (_, index) =>
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
  });

  test('answers a repeated identical vote with 204 and records it once', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const body = { pair: pair?.id, choice: 'tie', defects: { a: ['facts'], b: [] } };
    await call(from, 'POST', '/votes', body);

    const response = await call(from, 'POST', '/votes', body);

    expect(response.status).toBe(204);
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
    await fillCap(0, options);
    const from = await admitted(TAG_IP);
    const { pair } = await pairOf(from);

    const response = await vote(from, pair?.id ?? '');

    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({ error: 'rate_limited' });
    expect(await pendingRow(pair?.id ?? '')).not.toBeNull();
  });

  test('counts only votes inside the window and those not undone toward the cap', async () => {
    await fillCap(1);
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
    ['current while the counted votes match', 'local', 0, 'local', 'current'],
    ['stale with a counted vote after the cutoff', 'local', 1, 'local', 'stale'],
    ['unknown without its own cutoff', 'local', 0, 'production', 'unknown'],
    ['unknown on staging', 'staging', 0, 'local', 'unknown'],
  ] as const)('answers %s', async (_, store, later, closed, expected) => {
    await seedVote(WRITING, caseKey, systems);
    await seedVote(WRITING, caseKey, systems);
    const { ids } = await read();
    const cutoff = ids[ids.length - 1 - later];

    const status = await freshness(env.DB, store, {
      family: WRITING,
      [closed]: String(cutoff),
      [`${closed}_votes`]: String(ids.length - later),
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
    const query = { family: WRITING, local: String(ids[1]), local_votes: String(counted.length) };
    expect(await freshness(env.DB, 'local', query)).toBe('current');

    await env.DB.prepare('UPDATE votes SET exclusion = ? WHERE id = ?').bind(after, ids[0]).run();

    expect(await freshness(env.DB, 'local', query)).toBe('stale');
  });

  test('leaves excluded votes after the cutoff out', async () => {
    await seedVote(WRITING, caseKey, systems, { exclusion: 'trash' });
    const from = await admitted();

    const response = await call(from, 'GET', `/freshness?family=${WRITING}&local=0&local_votes=0`);

    expect(await response.json()).toEqual({ status: 'current' });
  });

  test('rejects a cutoff without its counted votes', async () => {
    const from = await admitted();

    const response = await call(from, 'GET', `/freshness?family=${WRITING}&local=0`);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: 'invalid_request' });
  });
});
