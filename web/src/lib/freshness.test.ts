// @vitest-environment jsdom
import { describe, expect, test, vi } from 'vitest';

import { queryFreshness } from '@/lib/freshness';

describe('queryFreshness', () => {
  test.each([undefined, 'https://preview.example.com'])(
    'does not contact a live service from an unrelated origin %s',
    async (origin) => {
      const fetch = vi.fn();
      vi.stubGlobal('fetch', fetch);

      const status = await queryFreshness('capture', origin, new AbortController().signal);

      expect(status).toBe('unknown');
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  test.each(['current', 'stale', 'unknown'] as const)(
    'reads the recorded export status %s through the same origin',
    async (expected) => {
      const fetch = vi.fn().mockResolvedValue(Response.json({ status: expected }));
      vi.stubGlobal('fetch', fetch);
      const signal = new AbortController().signal;

      const status = await queryFreshness('captured-export', window.location.origin, signal);

      expect(status).toBe(expected);
      expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/freshness', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ export_id: 'captured-export' }),
        signal,
      });
    },
  );

  test.each([
    ['unsupported status', Response.json({ status: 'pending' })],
    ['missing status', Response.json({})],
    ['extra private fields', Response.json({ status: 'current', actor: 'private' })],
    ['non-object body', Response.json('current')],
    ['invalid JSON', new Response('not JSON')],
    ['HTTP failure', new Response('Unavailable', { status: 503 })],
  ])('marks %s as unknown', async (_, response) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    const status = await queryFreshness(
      'capture',
      window.location.origin,
      new AbortController().signal,
    );

    expect(status).toBe('unknown');
  });

  test.each([
    ['network failure', new TypeError('Network unavailable')],
    ['cancelled request', new DOMException('Aborted', 'AbortError')],
  ])('marks a %s as unknown', async (_, error) => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error));

    const status = await queryFreshness(
      'capture',
      window.location.origin,
      new AbortController().signal,
    );

    expect(status).toBe('unknown');
  });
});
