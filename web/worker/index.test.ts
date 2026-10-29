import { env, exports } from 'cloudflare:workers';
import { describe, expect, test, vi } from 'vitest';

import {
  admitted,
  browser,
  call,
  CODING,
  ORIGIN,
  pairOf,
  publish,
  stubSiteverify,
  vote,
  WRITING,
} from './test/support';

const BLIND = 'writing-blind';

describe('GET /api/families', () => {
  test('lists each active publication without its systems', async () => {
    const response = await call(browser(), 'GET', '/families');

    expect(await response.json()).toEqual({
      families: [
        { family: CODING, display_name: 'Fixture', aspect: 'coding' },
        { family: WRITING, display_name: 'Fixture', aspect: 'writing' },
      ],
    });
  });
});

describe('blinding', () => {
  test('keeps systems, fingerprints, output keys and timings out of every response', async () => {
    await publish(BLIND, {
      first: { 'acme-large': ['One reply.'], 'acme-small': ['Another reply.'] },
      second: { 'acme-large': ['A third.'], 'acme-small': ['A fourth.'], 'acme-pro': [null] },
    });
    const outputs = await env.DB.prepare('SELECT output_key FROM outputs').all<{
      output_key: string;
    }>();
    const forbidden = [
      'acme-',
      'Acme ',
      'fp-',
      'issued_at',
      'elapsed_ms',
      ...outputs.results.map(({ output_key }) => output_key),
    ];
    const from = browser('192.0.2.50');
    const responses: Response[] = [];
    const send = async (...args: Parameters<typeof call>) => {
      const response = await call(...args);
      responses.push(response.clone());
      return response;
    };

    await send(from, 'GET', `/pair?family=${WRITING}`);
    stubSiteverify({ success: true, hostname: env.TURNSTILE_HOSTNAME });
    await send(from, 'POST', '/actors', { token: 'token' });
    await send(from, 'GET', '/families');
    const pairId = async (response: Response) =>
      (await response.json<{ pair: { id: string } }>()).pair.id;
    const drawn = await pairId(await send(from, 'GET', `/pair?family=${BLIND}`));
    const skipped = await pairId(await send(from, 'POST', '/pair/skip', { pair: drawn }));
    for (const pair of [drawn, skipped, skipped]) {
      await send(from, 'POST', '/votes', { pair, choice: 'a', defects: { a: [], b: [] } });
    }
    await send(from, 'POST', '/votes/undo', { family: BLIND });
    await send(from, 'POST', '/votes/undo', { family: BLIND });
    await send(from, 'GET', `/freshness?family=${BLIND}&local=0&local_votes=0`);

    for (const response of responses) {
      const text = [
        await response.text(),
        ...[...response.headers].map(([name, value]) => `${name}: ${value}`),
      ].join('\n');
      for (const value of forbidden) {
        expect(text, response.url).not.toContain(value);
      }
    }
    expect(responses.map(({ status }) => status)).toEqual(
      expect.arrayContaining([200, 204, 401, 409]),
    );
  });
});

describe('requests', () => {
  test.each([
    ['a foreign Origin', { Origin: 'https://example.com' }],
    ['no Origin', { Origin: '' }],
  ])('refuse a POST with %s', async (_, headers) => {
    const from = await admitted();
    const { pair } = await pairOf(from);

    const response = await call(
      from,
      'POST',
      '/votes',
      { pair: pair?.id, choice: 'a', defects: { a: [], b: [] } },
      headers,
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: 'forbidden' });
  });

  test('refuse a body over 16 KB', async () => {
    const response = await call(browser(), 'POST', '/actors', { token: 'x'.repeat(17 * 1024) });

    expect(response.status).toBe(413);
    expect(await response.json()).toEqual({ error: 'too_large' });
  });

  test.each([
    ['a malformed body', '/votes', '{'],
    ['an unknown field', '/votes/undo', JSON.stringify({ family: WRITING, extra: 1 })],
    ['an invalid family', '/votes/undo', JSON.stringify({ family: '../x' })],
  ])('answer %s with invalid_request', async (_, path, body) => {
    const from = await admitted();

    const response = await exports.default.fetch(
      new Request(`${ORIGIN}/api${path}`, {
        method: 'POST',
        headers: {
          'CF-Connecting-IP': from.ip,
          'Origin': ORIGIN,
          'Content-Type': 'application/json',
          'Cookie': from.cookie ?? '',
        },
        body,
      }),
    );

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: 'invalid_request' });
  });

  test('answer an unknown route with not_found and no-store', async () => {
    const response = await call(browser(), 'GET', '/unknown');

    expect(response.status).toBe(404);
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(await response.json()).toEqual({ error: 'not_found' });
  });

  test('stop a browser after 60 requests a minute', async () => {
    const from = browser('192.0.2.99');
    const statuses: number[] = [];
    for (let index = 0; index < 61; index++) {
      statuses.push((await call(from, 'GET', '/families')).status);
    }

    expect(statuses.slice(0, 60).every((status) => status === 200)).toBe(true);
    expect(statuses[60]).toBe(429);
  });

  test('log an unexpected failure by route alone and answer unavailable', async () => {
    const from = await admitted('203.0.113.5');
    const { pair } = await pairOf(from);
    const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(env.DB, 'batch').mockRejectedValueOnce(new Error('D1 is down.'));

    const response = await vote(from, pair?.id ?? '');

    expect(response.status).toBe(500);
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(await response.json()).toEqual({ error: 'unavailable' });
    expect(logged).toHaveBeenCalledWith({
      route: 'POST /api/votes',
      message: 'D1 is down.',
      stack: expect.any(String) as string,
    });
    const text = JSON.stringify(logged.mock.calls);
    for (const value of ['203.0.113.5', from.cookie ?? '', pair?.id ?? '']) {
      expect(text).not.toContain(value);
    }
  });
});
