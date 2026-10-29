import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

import { candidates, next, ranker, type PassingOutput } from './pairs';
import {
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

const SYNTHETIC = 'writing-synthetic';

/** Three systems with differing outputs on each of `count` cases. */
function threeWay(count: number) {
  return Object.fromEntries(
    Array.from({ length: count }, (_, index) => [
      `case-${index}`,
      {
        'acme-large': [`large ${index}`],
        'acme-pro': [`pro ${index}`],
        'acme-small': [`small ${index}`],
      },
    ]),
  );
}

const systemsOf = (row: { a_system: string; b_system: string } | null) =>
  row === null ? null : [row.a_system, row.b_system].sort();

describe('candidates', () => {
  const output = (case_key: string, system: string, content_digest: string, index = 0) =>
    ({
      output_key: `${case_key}/${system}/${index}`,
      case_key,
      system,
      content_digest,
    }) satisfies PassingOutput;

  test('pairs systems on a case only through samples that differ, skipping voted comparisons', () => {
    const outputs = [
      output('first', 'large', 'x'),
      output('first', 'large', 'y', 1),
      output('first', 'small', 'x'),
      output('first', 'pro', 'z'),
      output('second', 'large', 'same'),
      output('second', 'small', 'same'),
      output('third', 'large', 'alone'),
    ];

    const found = candidates(outputs, [{ case_key: 'first', a_system: 'pro', b_system: 'large' }]);

    expect(
      found.map(({ caseKey, systems, samples }) => ({
        caseKey,
        systems,
        samples: samples.map(([a, b]) => [a.output_key, b.output_key]),
      })),
    ).toEqual([
      {
        caseKey: 'first',
        systems: ['large', 'small'],
        samples: [['first/large/1', 'first/small/0']],
      },
      { caseKey: 'first', systems: ['pro', 'small'], samples: [['first/pro/0', 'first/small/0']] },
    ]);
  });
});

describe('next', () => {
  test('orders one system pair differently for different voters', async () => {
    const comparisons = candidates(
      Object.keys(threeWay(8)).flatMap((caseKey) => [
        { output_key: `${caseKey}/a`, case_key: caseKey, system: 'a', content_digest: 'a' },
        { output_key: `${caseKey}/b`, case_key: caseKey, system: 'b', content_digest: 'b' },
      ]),
      [],
    );
    const order = async (actor: string) => {
      const rank = ranker('key', actor, 'family');
      const seen = [comparisons[0].caseKey];
      for (let step = 1; step < comparisons.length; step++) {
        const drawn = await next(rank, comparisons, seen[seen.length - 1]);
        seen.push(drawn?.caseKey ?? 'none');
      }
      return seen;
    };

    const first = await order('first-actor');
    const second = await order('second-actor');

    expect(new Set(first).size).toBe(comparisons.length);
    expect(first).not.toEqual(second);
  });
});

describe('GET /api/pair', () => {
  test('draws the system pair with the fewest counted votes in the family', async () => {
    await publish(SYNTHETIC, threeWay(3));
    for (const caseKey of ['case-0', 'case-1']) {
      await seedVote(SYNTHETIC, caseKey, ['acme-large', 'acme-pro']);
      await seedVote(SYNTHETIC, caseKey, ['acme-large', 'acme-small']);
    }
    for (const caseKey of ['case-0', 'case-1', 'case-2']) {
      await seedVote(SYNTHETIC, caseKey, ['acme-pro', 'acme-small'], { exclusion: 'trash' });
    }
    const from = await admitted();

    const { pair } = await pairOf(from, SYNTHETIC);

    expect(systemsOf(await pendingRow(pair?.id ?? ''))).toEqual(['acme-pro', 'acme-small']);
  });

  test('shows the pair with its prompt, panes and defects, counting the remaining comparisons', async () => {
    const from = await admitted();

    const state = await pairOf(from, WRITING);

    expect(state).toEqual({
      pair: {
        id: expect.stringMatching(/^[0-9a-f-]{36}$/) as string,
        instruction: 'Write about first.',
        checklist: ['Mentions first'],
        category: 'docs',
        render: 'text',
        a: expect.stringMatching(/^acme-\w+ on first$/) as string,
        b: expect.stringMatching(/^acme-\w+ on first$/) as string,
        defects: ['scope', 'facts', 'clarity', 'tone', 'mechanics', 'formulaic', 'length'],
      },
      votes: 0,
      remaining: 3,
      undo: false,
    });
  });

  test('returns the pending pair again until a vote releases it', async () => {
    const from = await admitted();

    const first = await pairOf(from);
    const second = await pairOf(from);

    expect(second).toEqual(first);
  });

  test('draws the same pair again from the same state', async () => {
    await publish(SYNTHETIC, threeWay(4));
    const from = await admitted();
    const first = await pairOf(from, SYNTHETIC);
    const drawn = await pendingRow(first.pair?.id ?? '');
    await env.DB.prepare('DELETE FROM pairs').run();

    const again = await pairOf(from, SYNTHETIC);

    expect(again.pair?.id).not.toBe(first.pair?.id);
    expect({ ...again.pair, id: null }).toEqual({ ...first.pair, id: null });
    expect(await pendingRow(again.pair?.id ?? '')).toEqual({
      ...drawn,
      issued_at: expect.any(Number) as number,
    });
  });

  test('draws the same next pair after an undo and the same vote', async () => {
    await publish(SYNTHETIC, threeWay(4));
    const from = await admitted();
    const { pair } = await pairOf(from, SYNTHETIC);
    await vote(from, pair?.id ?? '');
    const following = await pendingRow((await pairOf(from, SYNTHETIC)).pair?.id ?? '');
    await call(from, 'POST', '/votes/undo', { family: SYNTHETIC });

    expect((await pairOf(from, SYNTHETIC)).pair?.id).toBe(pair?.id);
    await vote(from, pair?.id ?? '');
    const repeated = await pendingRow((await pairOf(from, SYNTHETIC)).pair?.id ?? '');

    expect(repeated).toEqual({ ...following, issued_at: expect.any(Number) as number });
  });

  test('redraws a pending pair whose publication was replaced', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    await publish(WRITING, threeWay(2), 'replacement');

    const state = await pairOf(from);

    expect(state.pair?.id).not.toBe(pair?.id);
    expect(state.pair?.instruction).toMatch(/^Instruction case-\d$/);
    expect(state.remaining).toBe(6);
  });

  test('answers null once the actor has voted on every comparison', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from, CODING);
    await vote(from, pair?.id ?? '');

    const state = await pairOf(from, CODING);

    expect(state).toEqual({ pair: null, votes: 1, remaining: 0, undo: true });
  });

  test("shows the same pair and totals after the owner excludes the actor's vote", async () => {
    const from = await admitted();
    await vote(from, (await pairOf(from)).pair?.id ?? '');
    await env.DB.prepare('UPDATE votes SET recorded_at = 0').run();
    const before = await pairOf(from);

    await env.DB.prepare("UPDATE votes SET exclusion = 'trash'").run();
    const after = await pairOf(from);

    expect(after).toEqual(before);
    expect(after).toMatchObject({ votes: 1, remaining: 2 });
  });

  test('answers 404 for a family without an active publication', async () => {
    const from = await admitted();

    const response = await call(from, 'GET', '/pair?family=missing');

    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: 'no_round' });
  });
});

describe('POST /api/pair/skip', () => {
  test('keeps the system pair through every case and wraps around, under new IDs', async () => {
    await publish(SYNTHETIC, threeWay(4));
    const from = await admitted();
    const { pair } = await pairOf(from, SYNTHETIC);
    const first = await pendingRow(pair?.id ?? '');
    const ids = [pair?.id];
    const cases = [first?.case_key];

    for (let step = 0; step < 4; step++) {
      const response = await call(from, 'POST', '/pair/skip', { pair: ids.at(-1) });
      expect(response.status).toBe(200);
      const id = (await response.json<{ pair: { id: string } }>()).pair.id;
      const row = await pendingRow(id);
      expect(systemsOf(row)).toEqual(systemsOf(first));
      ids.push(id);
      cases.push(row?.case_key);
    }

    expect(new Set(ids).size).toBe(5);
    expect(new Set(cases.slice(0, 4)).size).toBe(4);
    expect(cases[4]).toBe(cases[0]);
    expect(await env.DB.prepare('SELECT count(*) AS n FROM votes').first('n')).toBe(0);
  });

  test('keeps the pair when its system pair has no other case', async () => {
    const from = await admitted();
    const before = await pairOf(from);

    const response = await call(from, 'POST', '/pair/skip', { pair: before.pair?.id });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(before);
  });

  test('answers stale_pair for a pair that is no longer pending', async () => {
    const from = await admitted();
    const { pair } = await pairOf(from);
    await vote(from, pair?.id ?? '');

    const response = await call(from, 'POST', '/pair/skip', { pair: pair?.id });

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'stale_pair' });
  });

  test("answers stale_pair for another actor's pair", async () => {
    const owner = await admitted('192.0.2.1');
    const other = await admitted('192.0.2.2');
    const { pair } = await pairOf(owner);

    const response = await call(other, 'POST', '/pair/skip', { pair: pair?.id });

    expect(response.status).toBe(409);
  });
});
