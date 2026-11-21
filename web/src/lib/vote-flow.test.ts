import { afterEach, describe, expect, test, vi } from 'vitest';

import { UNDO_HEADER } from '@/lib/arena';

import { getPair, NetworkError } from './vote-flow';

const EMPTY = { pair: null, votes: 1, remaining: 0, undo: true };
const UNDO_PAIR = '00000000-0000-4000-8000-000000000001';

const transportFailure = () => Promise.reject(new TypeError('Failed to fetch'));
const gateway = (status: number) => () => Promise.resolve(new Response('gateway', { status }));
const interruptedBody = () =>
  Promise.resolve(
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{'));
          controller.error(new TypeError('The response body was interrupted'));
        },
      }),
    ),
  );

function stubFetch(first: () => Promise<Response>) {
  const fetch = vi.fn(first).mockImplementationOnce(first);
  vi.stubGlobal('fetch', fetch);
  vi.useFakeTimers();
  return fetch;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('getPair', () => {
  test.each([
    ['a rejected fetch', transportFailure],
    ['an interrupted response body', interruptedBody],
    ['HTTP 502', gateway(502)],
    ['HTTP 503', gateway(503)],
    ['HTTP 504', gateway(504)],
  ] as const)('retries %s with the same pair request', async (_, first) => {
    const fetch = stubFetch(first).mockResolvedValueOnce(
      Response.json(EMPTY, { headers: { [UNDO_HEADER]: UNDO_PAIR } }),
    );
    const { signal } = new AbortController();

    const state = getPair('writing-fixture', signal);
    await vi.advanceTimersByTimeAsync(399);
    expect(fetch).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);

    expect(await state).toEqual({ ...EMPTY, undoPair: UNDO_PAIR });
    expect(fetch.mock.calls).toEqual([
      ['/api/pair?family=writing-fixture', expect.objectContaining({ method: 'GET', signal })],
      ['/api/pair?family=writing-fixture', expect.objectContaining({ method: 'GET', signal })],
    ]);
  });

  test.each([
    [401, 'reload_required'],
    [403, 'forbidden'],
    [409, 'stale_pair'],
    [429, 'rate_limited'],
    [500, 'unavailable'],
  ] as const)('returns HTTP %s after one fetch', async (status, code) => {
    const fetch = stubFetch(() => Promise.resolve(Response.json({ error: code }, { status })));

    await expect(getPair('writing-fixture', new AbortController().signal)).rejects.toMatchObject({
      code,
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  test.each([
    ['invalid JSON', () => Promise.resolve(new Response('{'))],
    ['an invalid pair state', () => Promise.resolve(Response.json({}))],
    ['an HTML rate limit', gateway(429)],
  ] as const)('does not replay %s', async (_, first) => {
    const fetch = stubFetch(first);

    await expect(getPair('writing-fixture', new AbortController().signal)).rejects.toThrow(
      NetworkError,
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  test('ends after two fetches when both fail', async () => {
    const fetch = stubFetch(transportFailure);
    const result = expect(getPair('writing-fixture', new AbortController().signal)).rejects.toThrow(
      NetworkError,
    );

    await vi.advanceTimersByTimeAsync(400);

    await result;
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  test('preserves a rate limit received on the second attempt', async () => {
    const fetch = stubFetch(gateway(503)).mockResolvedValueOnce(
      Response.json({ error: 'rate_limited' }, { status: 429 }),
    );

    const result = expect(
      getPair('writing-fixture', new AbortController().signal),
    ).rejects.toMatchObject({ code: 'rate_limited' });

    await vi.advanceTimersByTimeAsync(400);

    await result;
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  test('cancels the delay without sending another request', async () => {
    const fetch = stubFetch(transportFailure);
    const controller = new AbortController();
    const result = expect(getPair('writing-fixture', controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(1);

    controller.abort();

    await result;
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
