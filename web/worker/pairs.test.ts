import { env } from 'cloudflare:workers';
import { describe, expect, test, vi } from 'vitest';

import { candidates, draw, next, ranker, type Comparison, type Rank } from './pairs';
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
  test('expands pair facts and skips voted comparisons regardless of their side order', () => {
    const facts = [
      { low_system: 'large', high_system: 'pro', case_keys: '["first", "second"]' },
      { low_system: 'large', high_system: 'small', case_keys: '["first"]' },
      { low_system: 'pro', high_system: 'small', case_keys: '["first"]' },
    ];

    const found = candidates(facts, [{ case_key: 'first', a_system: 'pro', b_system: 'large' }]);

    expect(found).toEqual([
      { caseKey: 'second', systems: ['large', 'pro'] },
      { caseKey: 'first', systems: ['large', 'small'] },
      { caseKey: 'first', systems: ['pro', 'small'] },
    ]);
  });

  test('normalizes SQL system order before returning comparisons and matching vote history', () => {
    const bmp = 'system-\ue000';
    const astral = 'system-\u{10000}';
    const facts = [{ low_system: bmp, high_system: astral, case_keys: '["first", "second"]' }];

    const found = candidates(facts, []);
    const remaining = candidates(facts, [{ case_key: 'first', a_system: bmp, b_system: astral }]);

    expect(found).toEqual([
      { caseKey: 'first', systems: [astral, bmp] },
      { caseKey: 'second', systems: [astral, bmp] },
    ]);
    expect(remaining).toEqual([{ caseKey: 'second', systems: [astral, bmp] }]);
  });
});

describe('draw', () => {
  const comparison = (caseKey: string, low: string, high: string): Comparison => ({
    caseKey,
    systems: [low, high],
  });

  /** A voter's order given as fixed ranks, with every unlisted item ranked last. */
  const fixed =
    (ranks: Record<string, string>): Rank =>
    (...parts) =>
      Promise.resolve(ranks[parts.join(' ')] ?? 'f0');

  test('draws the least covered system pair over the one the voter ranks first', async () => {
    const comparisons = [
      comparison('first', 'large', 'pro'),
      comparison('first', 'large', 'small'),
      comparison('first', 'pro', 'small'),
    ];
    const rank = fixed({ 'pair large pro': '00', 'pair large small': '10' });
    const coverage = new Map([
      [JSON.stringify(['large', 'pro']), 1],
      [JSON.stringify(['pro', 'small']), 1],
    ]);

    const drawn = await draw(rank, comparisons, coverage);

    expect(drawn).toEqual({ caseKey: 'first', systems: ['large', 'small'] });
  });

  test('breaks equal coverage by the keyed system-pair rank and then the keyed case rank', async () => {
    const rank = fixed({
      'pair large pro': '20',
      'pair large small': '10',
      'case large small first': '30',
      'case large small second': '00',
    });

    const drawn = await draw(
      rank,
      [
        comparison('first', 'large', 'pro'),
        comparison('first', 'large', 'small'),
        comparison('second', 'large', 'small'),
      ],
      new Map(),
    );

    expect(drawn).toEqual({ caseKey: 'second', systems: ['large', 'small'] });
  });
});

describe('next', () => {
  test('orders one system pair differently for different voters', async () => {
    const comparisons = candidates(
      [{ low_system: 'a', high_system: 'b', case_keys: JSON.stringify(Object.keys(threeWay(8))) }],
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
  test.each([
    '00000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-000000000017',
  ] as const)(
    'draws the lowest-ranked distinct-content sample pair for actor %s',
    async (actor) => {
      await publish(SYNTHETIC, {
        first: {
          'acme-large': ['shared', 'large first', 'large second'],
          'acme-small': ['shared', 'small first', 'small second'],
        },
      });
      const random = vi.spyOn(crypto, 'randomUUID').mockReturnValue(actor);
      const from = await admitted();
      random.mockRestore();
      const rank = ranker('test-actor-key', await actorOf(from), SYNTHETIC);
      const outputs = (
        await env.DB.prepare(
          "SELECT output_key, system, content_digest FROM outputs WHERE verdict = 'pass' AND publication_id = ?",
        )
          .bind(`${SYNTHETIC}-publication`)
          .all<{ output_key: string; system: string; content_digest: string }>()
      ).results;
      const ranked = await Promise.all(
        outputs
          .filter(({ system }) => system === 'acme-large')
          .flatMap((a) =>
            outputs
              .filter((b) => b.system === 'acme-small' && b.content_digest !== a.content_digest)
              .map(async (b) => ({ a, b, rank: await rank('sample', a.output_key, b.output_key) })),
          ),
      );
      ranked.sort((a, b) => a.rank.localeCompare(b.rank));
      const expected = ranked[0];
      const swapped = Number.parseInt(expected.rank.slice(-1), 16) & 1;

      const state = await pairOf(from, SYNTHETIC);
      const actual = await env.DB.prepare('SELECT a_output, b_output FROM pairs WHERE id = ?')
        .bind(state.pair?.id)
        .first();

      expect(actual).toEqual({
        a_output: (swapped ? expected.b : expected.a).output_key,
        b_output: (swapped ? expected.a : expected.b).output_key,
      });
      expect(state.pair?.render).toBe('text');
      if (state.pair?.render !== 'text') {
        throw new Error('Expected a text pair.');
      }
      expect(state.pair.a).not.toBe(state.pair.b);
    },
  );

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

  test('counts votes on Unicode system pairs before drawing the less covered alternative', async () => {
    const bmp = 'system-\ue000';
    const astral = 'system-\u{10000}';
    await publish(SYNTHETIC, {
      first: { [bmp]: ['First BMP.'], [astral]: ['First astral.'] },
      second: { 'acme-large': ['Second large.'], 'acme-small': ['Second small.'] },
    });
    await seedVote(SYNTHETIC, 'first', [bmp, astral]);
    const random = vi
      .spyOn(crypto, 'randomUUID')
      .mockReturnValue('00000000-0000-4000-8000-000000000001');
    const from = await admitted();
    random.mockRestore();

    const { pair } = await pairOf(from, SYNTHETIC);

    expect(systemsOf(await pendingRow(pair?.id ?? ''))).toEqual(['acme-large', 'acme-small']);
    expect(pair?.instruction).toBe('Instruction second');
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

  test('rejects a draw superseded during sample loading and preserves the newer pending pair', async () => {
    await publish(SYNTHETIC, threeWay(2));
    const from = await admitted();
    const prepare = env.DB.prepare.bind(env.DB);
    let replacement: string | undefined;
    let switched = false;
    vi.spyOn(env.DB, 'prepare').mockImplementation((query) => {
      const statement = prepare(query);
      if (query.includes('system IN (?, ?)')) {
        const bind = statement.bind.bind(statement);
        vi.spyOn(statement, 'bind').mockImplementation((...values) => {
          const bound = bind(...values);
          const all = bound.all.bind(bound);
          vi.spyOn(bound, 'all').mockImplementation(async <T>() => {
            const result = await all<T>();
            if (!switched) {
              switched = true;
              await publish(SYNTHETIC, threeWay(2), 'replacement');
              replacement = (await pairOf(from, SYNTHETIC)).pair?.id;
            }
            return result;
          });
          return bound;
        });
      }
      return statement;
    });

    const response = await call(from, 'GET', `/pair?family=${SYNTHETIC}`);

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'stale_pair' });
    expect(replacement).toEqual(expect.any(String));
    expect(
      await env.DB.prepare('SELECT id, publication_id FROM pairs WHERE family = ?')
        .bind(SYNTHETIC)
        .all(),
    ).toMatchObject({ results: [{ id: replacement, publication_id: 'replacement' }] });
    expect((await pairOf(from, SYNTHETIC)).pair?.id).toBe(replacement);
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

  test('reads the passing outputs once', async () => {
    await publish(SYNTHETIC, threeWay(4));
    const from = await admitted();
    const { pair } = await pairOf(from, SYNTHETIC);
    const prepare = vi.spyOn(env.DB, 'prepare');

    const response = await call(from, 'POST', '/pair/skip', { pair: pair?.id });

    expect(response.status).toBe(200);
    expect(prepare.mock.calls.filter(([query]) => query.includes("verdict = 'pass'"))).toHaveLength(
      1,
    );
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
