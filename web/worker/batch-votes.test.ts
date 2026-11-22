import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';
import * as z from 'zod/mini';

import {
  MAX_BATCH_VOTES,
  pairBatchView,
  pairState,
  voteBatchView,
  type VoteRequest,
} from '@/lib/arena';

import { PAIR_RETENTION_MS } from './limits';
import { retain } from './retention';
import {
  admitted,
  browser,
  call,
  CODING,
  pairOf,
  publish,
  WRITING,
  type Browser,
} from './test/support';

const ballot = (pair: string): VoteRequest => ({
  pair,
  choice: 'a',
  defects: { a: [], b: [] },
});

async function offered(from: Browser, count = MAX_BATCH_VOTES) {
  const response = await call(from, 'POST', '/pair/batch', { family: WRITING, count });
  expect(response.status).toBe(200);
  return z.parse(pairBatchView, await response.json()).pairs;
}

async function submit(from: Browser, votes: VoteRequest[], family = WRITING) {
  const response = await call(from, 'POST', '/votes/batch', { family, votes });
  expect(response.status).toBe(200);
  return z.parse(voteBatchView, await response.json()).votes;
}

describe('batch voting', () => {
  test('reserves distinct comparisons and shares pending receipts with ordinary voting', async () => {
    const from = await admitted();
    const ordinary = (await pairOf(from)).pair;

    const [first, retry] = await Promise.all([offered(from), offered(from)]);

    expect(first).toEqual(retry);
    expect(first).toHaveLength(3);
    expect(first.at(0)).toEqual(ordinary);
    expect(new Set(first.map(({ id }) => id)).size).toBe(3);
    const comparisons = await env.DB.prepare(
      `SELECT pairs.case_key, min(a.system, b.system) AS low, max(a.system, b.system) AS high
       FROM pending_pairs AS pairs
       JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
       JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
       GROUP BY pairs.case_key, low, high`,
    ).all();
    expect(comparisons.results).toHaveLength(3);
  });

  test('skips the current batch slot without changing its systems or position', async () => {
    await publish(WRITING, {
      first: { large: ['First large.'], pro: ['First pro.'], small: ['First small.'] },
      second: { large: ['Second large.'], pro: ['Second pro.'], small: ['Second small.'] },
    });
    const from = await admitted();
    const [first, second] = await offered(from, 2);
    await env.DB.prepare('UPDATE batch_pairs SET issued_at = 0').run();
    const comparison = (id: string) =>
      env.DB.prepare(
        `SELECT min(a.system, b.system) AS low, max(a.system, b.system) AS high
         FROM batch_pairs AS pairs
         JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
         JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
         WHERE pairs.id = ?`,
      )
        .bind(id)
        .first();
    const systems = await comparison(first.id);

    const response = await call(from, 'POST', '/pair/skip', { pair: first.id });

    expect(response.status).toBe(200);
    const { pair } = z.parse(pairState, await response.json());
    expect(pair?.id).not.toBe(first.id);
    expect(pair?.instruction).not.toBe(first.instruction);
    expect(await comparison(pair?.id ?? '')).toEqual(systems);
    expect((await pairOf(from)).pair).toEqual(pair);
    expect(await offered(from, 2)).toEqual([pair, second]);
  });

  test('records eligible items together and preserves exact retries and undo', async () => {
    const from = await admitted();
    const [first, invalid, last] = await offered(from);
    const requests: VoteRequest[] = [
      ballot(first.id),
      { ...ballot(invalid.id), defects: { a: ['testing'], b: [] } },
      ballot(last.id),
    ];

    const [statuses, retry] = await Promise.all([submit(from, requests), submit(from, requests)]);

    expect([statuses, retry].map((items) => items.map(({ status }) => status)).sort()).toEqual([
      ['identical', 'invalid_request', 'identical'],
      ['recorded', 'invalid_request', 'recorded'],
    ]);
    const stored = await env.DB.prepare('SELECT pair_id, recorded_at FROM votes ORDER BY id').all();
    expect(stored.results.map(({ pair_id }) => pair_id)).toEqual([first.id, last.id]);
    expect(new Set(stored.results.map(({ recorded_at }) => recorded_at)).size).toBe(1);

    const undo = await call(from, 'POST', '/votes/undo', { family: WRITING, pair: last.id });
    expect(undo.status).toBe(204);
    const restored = (await pairOf(from)).pair;
    expect(restored?.id).not.toBe(last.id);
    expect(restored).toEqual({ ...last, id: restored?.id });
    expect((await submit(from, [ballot(last.id)])).at(0)?.status).toBe('stale_pair');
    expect((await submit(from, [ballot(restored?.id ?? '')])).at(0)?.status).toBe('recorded');
  });

  test('checks the admitted actor, family and active publication for each receipt', async () => {
    const from = await admitted();
    const other = await admitted('192.0.2.29');
    const [mine] = await offered(from);
    const [foreign] = await offered(other);
    const unknown = crypto.randomUUID();

    expect(
      (await submit(from, [ballot(foreign.id), ballot(unknown)])).map(({ status }) => status),
    ).toEqual(['stale_pair', 'stale_pair']);
    expect((await submit(from, [ballot(mine.id)], CODING)).at(0)?.status).toBe('stale_pair');
    await publish(WRITING, { only: { 'acme-large': ['first'], 'acme-small': ['second'] } }, 'next');
    expect((await submit(from, [ballot(mine.id)])).at(0)?.status).toBe('stale_pair');
    expect((await offered(from)).map(({ id }) => id)).not.toContain(mine.id);
  });

  test('invalidates both forms of receipt on activation and preserves ballots on rollback', async () => {
    const cases = {
      first: { large: ['First large.'], pro: ['First pro.'], small: ['First small.'] },
    };
    await publish(WRITING, cases, 'original');
    const from = await admitted();
    await pairOf(from);
    const old = await offered(from);
    const publication = await env.DB.prepare('SELECT publication_id FROM active WHERE family = ?')
      .bind(WRITING)
      .first('publication_id');

    await publish(WRITING, cases, 'replacement');
    const [replacement] = await offered(from, 1);
    expect((await submit(from, [ballot(replacement.id)]))[0].status).toBe('recorded');
    await env.DB.prepare('UPDATE active SET publication_id = ? WHERE family = ?')
      .bind(publication, WRITING)
      .run();

    const current = await offered(from);
    expect(current).toHaveLength(2);
    expect(
      (
        await submit(
          from,
          old.map(({ id }) => ballot(id)),
        )
      ).map(({ status }) => status),
    ).toEqual(['stale_pair', 'stale_pair', 'stale_pair']);
    expect(
      (
        await submit(
          from,
          current.map(({ id }) => ballot(id)),
        )
      ).map(({ status }) => status),
    ).toEqual(['recorded', 'recorded']);
    expect(await offered(from)).toEqual([]);
  });

  test.each(['batch', 'ordinary'])(
    'retires the other receipt when the %s receipt wins',
    async (winner) => {
      const from = await admitted();
      const [first] = await offered(from, 1);
      const ordinary = crypto.randomUUID();
      // A preceding Worker can still issue an ordinary receipt while a batch slot exists.
      await env.DB.prepare(
        `INSERT INTO pairs (id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at)
       SELECT ?, actor_id, family, publication_id, case_key, a_output, b_output, issued_at
       FROM batch_pairs WHERE id = ?`,
      )
        .bind(ordinary, first.id)
        .run();
      const [selected, redundant] =
        winner === 'batch' ? [first.id, ordinary] : [ordinary, first.id];

      expect((await submit(from, [ballot(selected)]))[0].status).toBe('recorded');
      expect((await submit(from, [ballot(redundant)]))[0].status).toBe('stale_pair');
      expect(await env.DB.prepare('SELECT id FROM pending_pairs').first()).toBeNull();
      expect(await offered(from)).toHaveLength(2);
    },
  );

  test('clears expired batch receipts and reissues the comparisons under fresh IDs', async () => {
    const from = await admitted();
    const first = await offered(from);
    await retain(env.DB, Date.now() + PAIR_RETENTION_MS + 1);

    const renewed = await offered(from);
    const replay = await submit(
      from,
      first.map(({ id }) => ballot(id)),
    );

    expect(renewed.map((pair) => ({ ...pair, id: '' }))).toEqual(
      first.map((pair) => ({ ...pair, id: '' })),
    );
    expect(renewed.map(({ id }) => id)).not.toEqual(first.map(({ id }) => id));
    expect(replay.map(({ status }) => status)).toEqual(first.map(() => 'stale_pair'));
  });

  test('enforces admission, the per-request count and the body limit', async () => {
    const anonymous = browser();
    const from = await admitted('192.0.2.51');
    const request = ballot(crypto.randomUUID());
    const tooMany = Array.from({ length: MAX_BATCH_VOTES + 1 }, () => ballot(crypto.randomUUID()));

    const responses = await Promise.all([
      call(anonymous, 'POST', '/pair/batch', { family: WRITING, count: 1 }),
      call(anonymous, 'POST', '/votes/batch', { family: WRITING, votes: [request] }),
      call(from, 'POST', '/pair/batch', { family: WRITING, count: MAX_BATCH_VOTES + 1 }),
      call(from, 'POST', '/votes/batch', { family: WRITING, votes: tooMany }),
      call(from, 'POST', '/votes/batch', { family: WRITING, votes: [request, request] }),
      call(from, 'POST', '/votes/batch', {
        family: WRITING,
        votes: [
          { ...request, defects: { a: Array.from({ length: 3_000 }, () => 'facts'), b: [] } },
        ],
      }),
    ]);

    expect(responses.map(({ status }) => status)).toEqual([401, 401, 400, 400, 400, 413]);
  });
});
