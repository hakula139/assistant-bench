import { useEffect, useState } from 'react';

import { note } from '@/components/site/PageParts';
import { checkFreshness, type Freshness } from '@/lib/arena';
import type { Cutoffs } from '@/lib/report';

const closedTime = new Intl.DateTimeFormat('en', {
  dateStyle: 'medium',
  timeStyle: 'short',
  timeZone: 'UTC',
});

const messages: Record<Freshness, string> = {
  current: 'The ranking uses every counted vote.',
  stale: 'Votes or moderation have changed since this round closed.',
  unknown: 'Current votes could not be checked.',
};

interface SnapshotStatusProps {
  family: string;
  closedAt: string;
  cutoffs: Cutoffs;
}

/** A voted round's closing time, followed by the vote API's freshness verdict once it answers. */
export function SnapshotStatus({ family, closedAt, cutoffs }: SnapshotStatusProps) {
  const [status, setStatus] = useState<Freshness | null>(null);
  const { production, local } = cutoffs;

  useEffect(() => {
    const controller = new AbortController();
    void checkFreshness(family, { production, local }, controller.signal)
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
  }, [family, production, local]);

  return (
    <p className={note} role="status">
      Closed <time dateTime={closedAt}>{closedTime.format(new Date(closedAt))} UTC</time>.
      {status !== null && ` ${messages[status]}`}
    </p>
  );
}
