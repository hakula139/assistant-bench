import { useEffect, useState } from 'react';

import { note } from '@/components/site/PageParts';
import { queryFreshness, type Freshness } from '@/lib/freshness';

const capturedTime = new Intl.DateTimeFormat('en', {
  dateStyle: 'medium',
  timeStyle: 'short',
  timeZone: 'UTC',
});

const messages: Record<Freshness, string> = {
  current: 'The ranking uses the current voting evidence.',
  stale: 'Votes, moderation or evaluation settings have changed since this capture.',
  unknown: 'Current voting evidence could not be checked.',
};

export function SnapshotStatus({
  exportId,
  capturedAt,
  liveOrigin,
}: {
  exportId: string;
  capturedAt: string;
  liveOrigin: string | undefined;
}) {
  const [status, setStatus] = useState<Freshness>('unknown');

  useEffect(() => {
    const controller = new AbortController();
    void queryFreshness(exportId, liveOrigin, controller.signal).then((value) => {
      if (!controller.signal.aborted) {
        setStatus(value);
      }
    });

    return () => {
      controller.abort();
    };
  }, [exportId, liveOrigin]);

  return (
    <p className={note} role="status">
      Captured <time dateTime={capturedAt}>{capturedTime.format(new Date(capturedAt))} UTC</time>.{' '}
      {messages[status]}
    </p>
  );
}
