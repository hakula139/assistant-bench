// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { SnapshotStatus } from '@/components/boards/SnapshotStatus';

const closedAt = '2026-01-02T12:00:00Z';
const closed = 'Closed Jan 2, 2026, 12:00 PM UTC.';
const unknown = 'Closed Jan 2, 2026, 12:00 PM UTC. Current votes could not be checked.';
const cutoffs = { production: 337, local: null };

function stubFetch(response: Promise<Response>) {
  const fetch = vi.fn<typeof globalThis.fetch>().mockReturnValue(response);
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

function status() {
  return <SnapshotStatus family="writing-zh" closedAt={closedAt} cutoffs={cutoffs} />;
}

describe('SnapshotStatus', () => {
  afterEach(() => {
    cleanup();
  });

  test('renders only the closing time before hydration', () => {
    const html = renderToString(status());

    expect(html).toContain(`<time dateTime="${closedAt}">`);
    expect(html.replace(/<[^>]+>/g, '')).toBe(closed);
  });

  test.each([
    ['current', 'The ranking uses every counted vote.'],
    ['stale', 'Votes or moderation have changed since this round closed.'],
    ['unknown', 'Current votes could not be checked.'],
  ] as const)(
    'checks the serving origin and then shows the %s verdict',
    async (verdict, message) => {
      const pending = Promise.withResolvers<Response>();
      const fetch = stubFetch(pending.promise);

      render(status());
      const notice = screen.getByRole('status');
      expect(notice.textContent).toBe(closed);

      pending.resolve(Response.json({ status: verdict }));

      await screen.findByText(message, { exact: false });
      expect(notice.textContent).toBe(`${closed} ${message}`);
      expect(notice.querySelector('time')?.dateTime).toBe(closedAt);
      expect(fetch).toHaveBeenCalledOnce();
      const [path, { signal, ...init } = {}] = fetch.mock.calls[0];
      expect(path).toBe('/api/freshness?family=writing-zh&production=337');
      expect(init).toEqual({ method: 'GET', credentials: 'same-origin' });
      expect(signal).toBeInstanceOf(AbortSignal);
    },
  );

  test.each([
    [{ production: 337, local: 12 }, 'production=337&local=12'],
    [{ production: 0, local: null }, 'production=0'],
  ])('sends the cutoff of every store the round read in %j', (read, query) => {
    const fetch = stubFetch(Promise.withResolvers<Response>().promise);

    render(<SnapshotStatus family="writing-zh" closedAt={closedAt} cutoffs={read} />);

    expect(fetch.mock.calls[0][0]).toBe(`/api/freshness?family=writing-zh&${query}`);
  });

  test.each([
    ['an unsupported status', () => Promise.resolve(Response.json({ status: 'pending' }))],
    ['extra private fields', () => Promise.resolve(Response.json({ status: 'current', a: 1 }))],
    ['invalid JSON', () => Promise.resolve(new Response('not JSON'))],
    ['a static host without the API', () => Promise.resolve(new Response('', { status: 404 }))],
    ['a network failure', () => Promise.reject(new TypeError('Network unavailable'))],
  ])('reports that %s could not be checked', async (_, response) => {
    stubFetch(response());

    render(status());

    expect((await screen.findByText(/could not be checked/)).textContent).toBe(unknown);
  });

  test('cancels its check when the closed round is removed', () => {
    const pending = Promise.withResolvers<Response>();
    const fetch = stubFetch(pending.promise);
    const { unmount } = render(status());
    const signal = fetch.mock.calls[0][1]?.signal;
    expect(signal?.aborted).toBe(false);

    unmount();

    expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole('status')).toBeNull();
    pending.resolve(Response.json({ status: 'current' }));
  });
});
