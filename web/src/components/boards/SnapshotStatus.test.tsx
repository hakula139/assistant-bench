// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { SnapshotStatus } from '@/components/boards/SnapshotStatus';
import { queryFreshness, type Freshness } from '@/lib/freshness';

vi.mock('@/lib/freshness', () => ({ queryFreshness: vi.fn() }));

const capturedAt = '2026-01-02T12:00:00Z';

describe('SnapshotStatus', () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  test.each([
    ['current', 'The ranking uses the current voting evidence.'],
    ['stale', 'Votes, moderation or evaluation settings have changed since this capture.'],
    ['unknown', 'Current voting evidence could not be checked.'],
  ] as const)('shows the captured time and %s evidence status', async (status, message) => {
    const pending = Promise.withResolvers<Freshness>();
    vi.mocked(queryFreshness).mockReturnValue(pending.promise);
    render(<SnapshotStatus exportId="capture" capturedAt={capturedAt} liveOrigin={undefined} />);
    const notice = screen.getByRole('status');
    expect(notice.textContent).toContain('Current voting evidence could not be checked.');

    pending.resolve(status);

    await screen.findByText(message, { exact: false });
    expect(notice.querySelector('time')?.dateTime).toBe(capturedAt);
    expect(notice.textContent).toContain('UTC');
    expect(notice.textContent).not.toMatch(/refresh|deadline/i);
    expect(queryFreshness).toHaveBeenCalledExactlyOnceWith(
      'capture',
      undefined,
      expect.any(AbortSignal),
    );
  });

  test('cancels its query when the captured round is removed', () => {
    const pending = Promise.withResolvers<Freshness>();
    vi.mocked(queryFreshness).mockReturnValue(pending.promise);
    const { unmount } = render(
      <SnapshotStatus exportId="capture" capturedAt={capturedAt} liveOrigin={undefined} />,
    );
    const [, , signal] = vi.mocked(queryFreshness).mock.calls[0];
    expect(signal.aborted).toBe(false);

    unmount();

    expect(signal.aborted).toBe(true);
    expect(screen.queryByRole('status')).toBeNull();
    pending.resolve('current');
  });
});
