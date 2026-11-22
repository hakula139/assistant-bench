import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

import { UNDO_HEADER, UNDO_NONE } from '@/lib/arena';

import { ipTag } from './actors';
import { UNDO_MS } from './limits';
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

  test('records votes from both browsers after a network reaches the former quota', async () => {
    const ip = '198.51.100.9';
    const previousVotes = 2_000;
    const tag = await ipTag(env.IP_TAG_KEY, ip);
    await env.DB.batch([
      env.DB.prepare("INSERT INTO actors (id, created_at) VALUES ('filler', 0)"),
      env.DB.prepare(
        `WITH RECURSIVE slots(i) AS (VALUES (0) UNION ALL SELECT i + 1 FROM slots WHERE i + 1 < ?)
         INSERT INTO votes (pair_id, actor_id, family, publication_id, case_key, a_output,
           b_output, a_system, b_system, choice, defects, elapsed_ms, recorded_at, ip_tag)
         SELECT 'filler-' || i, 'filler', 'other', publication_id, case_key, output_key, output_key,
           'x', 'y' || i, 'a', '{"a": [], "b": []}', 0, ?, ?
         FROM slots CROSS JOIN (SELECT publication_id, case_key, output_key FROM outputs LIMIT 1)`,
      ).bind(previousVotes, Date.now(), tag),
    ]);
    const [first, second] = [await admitted(ip), await admitted(ip)];
    const firstPair = (await pairOf(first)).pair?.id ?? '';
    const secondPair = (await pairOf(second)).pair?.id ?? '';

    const responses = await Promise.all([vote(first, firstPair), vote(second, secondPair)]);

    expect(responses.map(({ status }) => status)).toEqual([204, 204]);
    expect(
      await env.DB.prepare('SELECT count(*) AS votes FROM votes WHERE ip_tag = ?')
        .bind(tag)
        .first('votes'),
    ).toBe(previousVotes + 2);
    expect(await pendingRow(firstPair)).toBeNull();
    expect(await pendingRow(secondPair)).toBeNull();
  });
});

describe('POST /api/votes/confirm', () => {
  test('confirms the same choice and issue sets', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const recorded = { pair: pair?.id, choice: 'b', defects: { a: ['tone', 'facts'], b: [] } };
    expect((await call(from, 'POST', '/votes', recorded)).status).toBe(204);
    const before = await storedVotes();
    const duplicate = { ...recorded, defects: { a: ['facts', 'tone', 'facts'], b: [] } };

    const receipt = await call(from, 'POST', '/votes/confirm', duplicate);

    expect(receipt.status).toBe(204);
    expect(await storedVotes()).toEqual(before);
    expect(await pendingRow(pair?.id ?? '')).toBeNull();
  });

  test('gives the same refusal for an absent, foreign or different vote', async () => {
    const from = await admitted();
    const other = await admitted('192.0.2.63');
    const { pair } = await pairOf(from);
    const body = { pair: pair?.id, choice: 'b', defects: { a: ['facts'], b: [] } };
    const missing = await call(from, 'POST', '/votes/confirm', body);
    expect(missing.status).toBe(409);
    expect(await missing.json()).toEqual({ error: 'stale_pair' });
    expect((await call(from, 'POST', '/votes', body)).status).toBe(204);
    await env.DB.prepare("UPDATE votes SET exclusion = 'trash'").run();

    const replies = await Promise.all([
      call(from, 'POST', '/votes/confirm', { ...body, choice: 'tie' }),
      call(from, 'POST', '/votes/confirm', { ...body, defects: { a: [], b: ['facts'] } }),
      call(other, 'POST', '/votes/confirm', body),
    ]);

    for (const reply of replies) {
      expect(reply.status).toBe(409);
      expect(await reply.json()).toEqual({ error: 'stale_pair' });
    }
    const receipt = await call(from, 'POST', '/votes/confirm', body);
    expect(receipt.status).toBe(204);
    expect(await storedVotes()).toMatchObject([{ pair_id: pair?.id, choice: 'b' }]);
    expect(await coverage()).toEqual([]);
  });

  test('leaves a pending pair untouched when an older Worker withdrew its vote', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    const body = { pair: pair?.id, choice: 'a', defects: { a: [], b: [] } };
    expect((await call(from, 'POST', '/votes', body)).status).toBe(204);
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO pairs
           (id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at)
         SELECT pair_id, actor_id, family, publication_id, case_key, a_output, b_output,
           recorded_at - elapsed_ms
         FROM votes WHERE pair_id = ?`,
      ).bind(pair?.id),
      env.DB.prepare('DELETE FROM votes WHERE pair_id = ?').bind(pair?.id),
    ]);
    const restored = await pendingRow(pair?.id ?? '');
    expect(restored).not.toBeNull();

    const receipt = await call(from, 'POST', '/votes/confirm', body);

    expect(receipt.status).toBe(409);
    expect(await receipt.json()).toEqual({ error: 'stale_pair' });
    expect(await storedVotes()).toEqual([]);
    expect(await pendingRow(pair?.id ?? '')).toEqual(restored);
    expect(await coverage()).toEqual([]);
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
    const pending = env.DB.prepare('SELECT id, publication_id FROM pairs WHERE family = ?').bind(
      WRITING,
    );
    const original = await pending.first('publication_id');
    await vote(from, pair?.id ?? '');
    await publish(WRITING, { only: { 'acme-large': ['x'], 'acme-small': ['y'] } }, 'next');

    const withdrawn = await call(from, 'POST', '/votes/undo', { family: WRITING, pair: pair?.id });

    expect(withdrawn.status).toBe(204);
    expect(await storedVotes()).toEqual([]);
    const restored = await pending.first();
    expect(restored?.id).not.toBe(pair?.id);
    expect(restored?.publication_id).toBe(original);

    const state = await pairOf(from);

    expect(state.pair?.id).not.toBe(restored?.id);
    expect(state.pair?.instruction).toBe('Instruction only');
  });
});

describe('pair vote counts', () => {
  const caseKey = '32be94015839ac0131d6874ffcc11c79f78e8059790f678d80fa7008a1286c22';
  const systems = ['acme-large', 'acme-small'] as const;

  test('counts reversed systems together and isolates families through moderation and deletion', async () => {
    await seedVote(WRITING, caseKey, systems);
    await seedVote(WRITING, caseKey, [systems[1], systems[0]]);
    await publish('other-writing', { other: { 'acme-large': ['a'], 'acme-small': ['b'] } });
    await seedVote('other-writing', 'other', systems, { exclusion: 'trash' });
    await seedVote(WRITING, caseKey, systems, { exclusion: 'trash' });
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
  });
});
