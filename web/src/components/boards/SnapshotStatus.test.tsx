// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { SnapshotStatus } from '@/components/boards/SnapshotStatus';

const capturedAt = '2026-01-02T12:00:00Z';
const captured = 'Captured Jan 2, 2026, 12:00 PM UTC.';
const unknown = 'Captured Jan 2, 2026, 12:00 PM UTC. Current voting evidence could not be checked.';

function stubFetch(response: Promise<Response>) {
  const fetch = vi.fn<typeof globalThis.fetch>().mockReturnValue(response);
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

describe('SnapshotStatus', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test('renders only the capture time before hydration', () => {
    const html = renderToString(<SnapshotStatus exportId="capture" capturedAt={capturedAt} />);

    expect(html).toContain(`<time dateTime="${capturedAt}">`);
    expect(html.replace(/<[^>]+>/g, '')).toBe(captured);
  });

  test.each([
    ['current', 'The ranking uses the current voting evidence.'],
    ['stale', 'Votes, moderation or evaluation settings have changed since this capture.'],
    ['unknown', 'Current voting evidence could not be checked.'],
  ] as const)(
    'checks the serving origin and then shows the %s verdict',
    async (status, message) => {
      const pending = Promise.withResolvers<Response>();
      const fetch = stubFetch(pending.promise);

      render(<SnapshotStatus exportId="capture" capturedAt={capturedAt} />);
      const notice = screen.getByRole('status');
      expect(notice.textContent).toBe(captured);

      pending.resolve(Response.json({ status }));

      await screen.findByText(message, { exact: false });
      expect(notice.textContent).toBe(`${captured} ${message}`);
      expect(notice.querySelector('time')?.dateTime).toBe(capturedAt);
      expect(fetch).toHaveBeenCalledOnce();
      const [path, { signal, ...init } = {}] = fetch.mock.calls[0];
      expect(path).toBe('/api/freshness');
      expect(init).toEqual({
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ export_id: 'capture' }),
      });
      expect(signal).toBeInstanceOf(AbortSignal);
    },
  );

  test.each([
    ['an unsupported status', () => Promise.resolve(Response.json({ status: 'pending' }))],
    ['extra private fields', () => Promise.resolve(Response.json({ status: 'current', a: 1 }))],
    ['invalid JSON', () => Promise.resolve(new Response('not JSON'))],
    ['a static host without the API', () => Promise.resolve(new Response('', { status: 404 }))],
    ['a network failure', () => Promise.reject(new TypeError('Network unavailable'))],
  ])('reports that %s could not be checked', async (_, response) => {
    stubFetch(response());

    render(<SnapshotStatus exportId="capture" capturedAt={capturedAt} />);

    expect((await screen.findByText(/could not be checked/)).textContent).toBe(unknown);
  });

  test('cancels its check when the captured round is removed', () => {
    const pending = Promise.withResolvers<Response>();
    const fetch = stubFetch(pending.promise);
    const { unmount } = render(<SnapshotStatus exportId="capture" capturedAt={capturedAt} />);
    const signal = fetch.mock.calls[0][1]?.signal;
    expect(signal?.aborted).toBe(false);

    unmount();

    expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole('status')).toBeNull();
    pending.resolve(Response.json({ status: 'current' }));
  });
});
