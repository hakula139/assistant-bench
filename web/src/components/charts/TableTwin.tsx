import type { ReactNode } from 'react';

import { Disclosure } from '@/components/site/Disclosure';

/** A chart's data as a table, behind a disclosure below the chart. */
export function TableTwin({ children }: { children: ReactNode }) {
  return <Disclosure summary="Show the data as a table">{children}</Disclosure>;
}
