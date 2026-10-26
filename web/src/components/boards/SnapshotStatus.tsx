import { useEffect, useState } from 'react';

import { note } from '@/components/site/PageParts';
import { checkFreshness, type Freshness } from '@/lib/arena';

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

/** A ledger round's capture time, followed by the vote API's freshness verdict once it answers. */
export function SnapshotStatus({ exportId, capturedAt }: { exportId: string; capturedAt: string }) {
  const [status, setStatus] = useState<Freshness | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void checkFreshness({ export_id: exportId }, controller.signal)
      .then(
        (view) => view.status,
        (): Freshness => 'unknown',
      )
      .then((value) => {
        if (!controller.signal.aborted) {
          setStatus(value);
        }
      });

    return () => {
      controller.abort();
    };
  }, [exportId]);

  return (
    <p className={note} role="status">
      Captured <time dateTime={capturedAt}>{capturedTime.format(new Date(capturedAt))} UTC</time>.
      {status !== null && ` ${messages[status]}`}
    </p>
  );
}
